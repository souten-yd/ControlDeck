"""取得の実行（同時に 1 回だけ・ソースごとに時間切れと失敗を記録して他を止めない）と、見張り（取得の間隔・日刊・週刊のまとめの時刻）。"""
from __future__ import annotations

import asyncio
import logging
import os
import threading
import time
from datetime import datetime, timezone

from app.news import channels, gist, rag, settings, sources, store
from app.news import db as news_db
from app.news.db import NewsItem

logger = logging.getLogger("control_deck.news.service")

FIRST_DELAY_SECONDS = 60
SOURCE_TIMEOUT_SECONDS = 45
MIN_GAP_SECONDS = 30          # 「取り直す」の連打を抑える
CONCURRENCY = 4

_running = threading.Lock()
_status: dict[str, dict] = {}
_last: dict = {"started": 0.0, "finished": None, "new": 0, "gist": None, "rag": None}


def status() -> dict:
    """ソースごとの直近の結果（{source_key: {label, kind, ok, skipped, error, count, at}}）と、最後の実行。"""
    return {"sources": [dict(v, source_key=k) for k, v in _status.items()], "last": dict(_last), "running": _running.locked()}


def _error_text(exc: BaseException) -> str:
    if isinstance(exc, asyncio.TimeoutError):
        return f"{SOURCE_TIMEOUT_SECONDS} 秒以内に取れませんでした"
    return f"{type(exc).__name__}: {str(exc)[:120]}".rstrip(": ")


async def _fetch_one(source: sources.Source, gate: asyncio.Semaphore) -> tuple[sources.Source, list[dict], dict]:
    at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    async with gate:
        try:
            items = await asyncio.wait_for(source.fetch(), SOURCE_TIMEOUT_SECONDS)
            return source, items, {"label": source.label, "kind": source.kind, "ok": True, "skipped": "", "error": "", "count": len(items), "at": at}
        except sources.SkippedSource as exc:
            return source, [], {"label": source.label, "kind": source.kind, "ok": True, "skipped": str(exc), "error": "", "count": 0, "at": at}
        except Exception as exc:  # noqa: BLE001 - 1 つのソースの失敗で他を止めない
            logger.info("ニュースの取得に失敗しました (%s): %s", source.key, exc)
            return source, [], {"label": source.label, "kind": source.kind, "ok": False, "skipped": "", "error": _error_text(exc), "count": 0, "at": at}


def _save(items: list[dict], on_prune: str = "keep") -> dict:
    with news_db.session() as db:
        result = store.upsert_items(db, items) if items else {"new": 0, "updated": 0, "changed": []}
        removed: list[str] = []
        result["pruned"] = store.prune(db, channels.load()["archive_keep"], removed if on_prune == "delete" else None)
        result["removed_urls"] = removed
        return result


def _reload(ids: list[int]) -> list[dict]:
    from sqlalchemy import select

    with news_db.session() as db:
        return [store.item_dict(r) for r in db.scalars(select(NewsItem).where(NewsItem.id.in_(ids or [0])))]


async def _after_save(saved: dict, cfg: dict) -> dict:
    """取った後の仕事（一言要約・RAG への取り込み・RAG の掃除）。失敗しても取得の結果は変えない（理由だけ返す）。"""
    info: dict = {"gist": {"done": 0, "error": ""}, "rag": {"added": 0, "error": ""}}
    changed = saved["changed"]
    try:
        info["gist"] = await gist.enrich(changed)
        if info["gist"]["done"]:
            changed = await asyncio.to_thread(_reload, [i["id"] for i in changed])
    except Exception as exc:  # noqa: BLE001
        logger.warning("一言要約に失敗しました: %s", exc)
        info["gist"]["error"] = _error_text(exc)
    if cfg["rag"]["enabled"]:
        try:
            chans = channels.load()["channels"]
            by_url = {i["url"]: [c["name"] for c in chans if channels.matches(c, kind=i["kind"], source_key=i["source_key"], title=i["title"], summary=i["summary"])]
                      for i in changed}
            info["rag"].update(await rag.ingest_items(changed, by_url, fulltext=cfg["rag"]["fulltext"]))
            if saved["removed_urls"]:
                info["rag"]["removed"] = await asyncio.to_thread(rag.delete_urls, saved["removed_urls"])
        except Exception as exc:  # noqa: BLE001 - 取得そのものは成功させる
            logger.warning("RAG への取り込みに失敗しました: %s", exc)
            info["rag"]["error"] = _error_text(exc)
    return info


async def refresh(*, force: bool = False) -> dict:
    """全ソースを取り直す。実行中・直前に取ったばかりなら取らない（force で間隔だけ無視）。
    {"started": bool, "reason": ..., "new": 件数, "failed": 失敗したソース数}。"""
    if not _running.acquire(blocking=False):
        return {"started": False, "reason": "running"}
    try:
        if not force and time.monotonic() - _last["started"] < MIN_GAP_SECONDS:
            return {"started": False, "reason": "recent"}
        _last["started"] = time.monotonic()
        cfg = sources.load_config()
        conf = settings.load()
        listing = sources.build_sources(cfg, channels.load()["channels"])
        gate = asyncio.Semaphore(CONCURRENCY)
        results = await asyncio.gather(*[_fetch_one(s, gate) for s in listing])
        items = [item for _, got, _ in results for item in got]
        saved = await asyncio.to_thread(_save, items, conf["rag"]["on_prune"])
        # 設定から外したソースの状態は残さない
        keep = {s.key for s in listing}
        for key in list(_status):
            if key not in keep:
                _status.pop(key)
        for source, _, info in results:
            _status[source.key] = info
        _last.update(finished=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), new=saved["new"])
        after = await _after_save(saved, conf)
        _last.update(gist=after["gist"], rag=after["rag"])
        return {"started": True, "reason": "", "new": saved["new"], "updated": saved["updated"], "pruned": saved["pruned"], "after": after,
                "failed": sum(1 for _, _, i in results if not i["ok"])}
    finally:
        _running.release()


TICK_SECONDS = 60


async def news_loop() -> None:
    """起動の少し後から、1 分ごとに見て、取得の間隔が来ていれば取り（取得しない時間帯は除く）、日刊・週刊のまとめの時刻が来ていれば作る。
    テストでは動かさない（CONTROL_DECK_NEWS_AUTO=0）。"""
    from app.news import digest

    await asyncio.sleep(FIRST_DELAY_SECONDS)
    next_fetch = 0.0
    while True:
        if os.environ.get("CONTROL_DECK_NEWS_AUTO") != "0":
            try:
                conf = settings.load()
                if time.monotonic() >= next_fetch and not settings.in_quiet(conf, datetime.now()):
                    result = await refresh(force=True)
                    if result.get("started"):
                        logger.info("ニュースを取得しました: 新着 %s 件・失敗 %s 件", result["new"], result["failed"])
                    next_fetch = time.monotonic() + channels.load()["interval_minutes"] * 60
            except Exception:  # noqa: BLE001 - 1 回の失敗で見張りを止めない
                logger.exception("ニュースの取得に失敗しました")
            try:
                for row in await digest.run_due():
                    logger.info("ニュースのまとめを作りました: %s %s", row.get("period"), row.get("day"))
            except Exception:  # noqa: BLE001
                logger.exception("ニュースのまとめを作れませんでした")
        await asyncio.sleep(TICK_SECONDS)
