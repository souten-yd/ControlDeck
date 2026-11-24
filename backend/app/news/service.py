"""取得の実行（同時に 1 回だけ・ソースごとに時間切れと失敗を記録して他を止めない）と、見張り（取得の間隔・日刊・週刊のまとめの時刻）。"""
from __future__ import annotations

import asyncio
import logging
import os
import threading
import time
from datetime import datetime, timezone

from app.news import channels, fetchlog, gist, hub_sync, imagegen, images, prep, rag, settings, sources, store
from app.news import db as news_db
from app.news.db import NewsItem

logger = logging.getLogger("control_deck.news.service")

FIRST_DELAY_SECONDS = 60
SOURCE_TIMEOUT_SECONDS = 45
MIN_GAP_SECONDS = 30          # 「取り直す」の連打を抑える
CONCURRENCY = 4

_running = threading.Lock()
_status: dict[str, dict] = {}
_last_fetch: dict[str, float] = {}      # source_key -> 最後に取りに行った時刻（time.monotonic。見張りが間隔の来たものを選ぶ）
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
        started = time.monotonic()

        def ms() -> int:
            return int((time.monotonic() - started) * 1000)
        try:
            items = await asyncio.wait_for(source.fetch(), SOURCE_TIMEOUT_SECONDS)
            return source, items, {"label": source.label, "kind": source.kind, "ok": True, "skipped": "", "error": "", "count": len(items), "at": at, "ms": ms(), "status": 200}
        except sources.SkippedSource as exc:
            return source, [], {"label": source.label, "kind": source.kind, "ok": True, "skipped": str(exc), "error": "", "count": 0, "at": at, "ms": ms(), "status": None}
        except Exception as exc:  # noqa: BLE001 - 1 つのソースの失敗で他を止めない
            logger.info("ニュースの取得に失敗しました (%s): %s", source.key, exc)
            text = _error_text(exc)
            return source, [], {"label": source.label, "kind": source.kind, "ok": False, "skipped": "", "error": text, "count": 0, "at": at, "ms": ms(),
                                "status": fetchlog.status_of(text)}


def _save(items: list[dict], on_prune: str = "keep") -> dict:
    with news_db.session() as db:
        result = store.upsert_items(db, items) if items else {"new": 0, "updated": 0, "changed": []}
        removed: list[str] = []
        result["pruned"] = store.prune(db, channels.load()["archive_keep"], removed if on_prune == "delete" else None)
        result["removed_urls"] = removed
        return result


def _log_results(results: list, new_urls: list[str]) -> None:
    """取得の履歴（news_fetch_log）へ 1 回ぶんを書く。書けなくても取得は成功（ログだけ）。"""
    fresh = set(new_urls)
    rows = []
    for source, got, info in results:
        at = datetime.strptime(info["at"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        rows.append({"source_key": source.key, "at": at, "ok": info["ok"], "status": info.get("status"), "count": info["count"],
                     "new": sum(1 for i in got if i["url"] in fresh), "ms": info.get("ms", 0), "error": info["error"], "skipped": info["skipped"]})
    try:
        with news_db.session() as db:
            fetchlog.record(db, rows)
    except Exception:  # noqa: BLE001
        logger.exception("取得の履歴を書けませんでした")


def _reload(ids: list[int]) -> list[dict]:
    from sqlalchemy import select

    with news_db.session() as db:
        return [store.item_dict(r) for r in db.scalars(select(NewsItem).where(NewsItem.id.in_(ids or [0])))]


async def ingest_rag(changed: list[dict], cfg: dict, removed_urls: list[str]) -> dict:
    """取り込んだ記事を RAG へ（設定が入のときだけ）。失敗しても取得・同期の結果は変えない（理由だけ返す）。ハブの同期（hub_sync.py）も同じ道を使う。"""
    info: dict = {"added": 0, "error": ""}
    if not cfg["rag"]["enabled"]:
        return info
    try:
        chans = channels.load()["channels"]
        by_url = {i["url"]: [c["name"] for c in chans if channels.matches(c, kind=i["kind"], source_key=i["source_key"], title=i["title"], summary=i["summary"])]
                  for i in changed}
        info.update(await rag.ingest_items(changed, by_url, fulltext=cfg["rag"]["fulltext"]))
        if removed_urls:
            info["removed"] = await asyncio.to_thread(rag.delete_urls, removed_urls)
    except Exception as exc:  # noqa: BLE001 - 取得そのものは成功させる
        logger.warning("RAG への取り込みに失敗しました: %s", exc)
        info["error"] = _error_text(exc)
    return info


async def _after_save(saved: dict, cfg: dict) -> dict:
    """取った後の仕事（一言要約・RAG への取り込み・RAG の掃除）。失敗しても取得の結果は変えない（理由だけ返す）。"""
    info: dict = {"gist": {"done": 0, "error": ""}, "prep": {"done": 0, "calls": 0, "error": ""}, "rag": {"added": 0, "error": ""}}
    changed = saved["changed"]
    try:
        info["gist"] = await gist.enrich(changed)
        prepared = await prep.run(changed)
        info["prep"] = prepared
        if prepared["error"] and not info["gist"]["error"]:
            info["gist"]["error"] = prepared["error"]
        if info["gist"]["done"] or prepared["done"]:
            changed = await asyncio.to_thread(_reload, [i["id"] for i in changed])
    except Exception as exc:  # noqa: BLE001
        logger.warning("一言要約に失敗しました: %s", exc)
        info["gist"]["error"] = _error_text(exc)
    info["rag"].update(await ingest_rag(changed, cfg, saved["removed_urls"]))
    try:
        info["imagegen"] = await asyncio.to_thread(imagegen.generate_pending, cfg)
    except Exception as exc:  # noqa: BLE001 - 画像が作れなくても取得は成功
        logger.warning("イメージ画像の生成に失敗しました: %s", exc)
        info["imagegen"] = {"done": 0, "failed": 0, "skipped": "", "error": _error_text(exc)}
    return info


async def refresh(*, force: bool = False, channel_ids: list[str] | None = None, keys: list[str] | None = None) -> dict:
    """全ソースを取り直す。実行中・直前に取ったばかりなら取らない（force で間隔だけ無視）。
    channel_ids を渡すと、そのチャンネルの検索（source_key が search:<id>:）だけを取り直す（チャンネルの語を変えた直後。ほかのソースの状態は触らない）。
    keys を渡すと、その source_key のソースだけ取る（見張りが、間隔の来たものだけ取るとき。ほかのソースの状態は触らない）。
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
        if channel_ids is not None:
            prefixes = tuple(f"search:{cid}:" for cid in channel_ids)
            listing = [s for s in listing if s.key.startswith(prefixes)]
        if keys is not None:
            wanted = set(keys)
            listing = [s for s in listing if s.key in wanted]
        gate = asyncio.Semaphore(CONCURRENCY)
        results = await asyncio.gather(*[_fetch_one(s, gate) for s in listing])
        items = [item for _, got, _ in results for item in got]
        saved = await asyncio.to_thread(_save, items, conf["rag"]["on_prune"])
        try:     # 画像の無い新着へ、記事のページの og:image を付ける（失敗しても取得は成功）
            saved["images"] = await images.fill(saved.get("new_ids", []))
        except Exception as exc:  # noqa: BLE001
            logger.warning("記事の画像の取得に失敗しました: %s", exc)
        # 設定から外したソースの状態は残さない
        if channel_ids is None and keys is None:
            keep = {s.key for s in listing}
            for key in list(_status):
                if key not in keep:
                    _status.pop(key)
        stamp = time.monotonic()
        for source, _, _ in results:
            _last_fetch[source.key] = stamp
        await asyncio.to_thread(_log_results, results, saved.get("new_urls", []))
        for source, _, info in results:
            # 最後に取れた時刻は、失敗しても前回の値を残す（画面の「最後に取れた」「最後の失敗」）
            info["ok_at"] = info["at"] if info["ok"] and not info["skipped"] else (_status.get(source.key) or {}).get("ok_at", "")
            info["last_error"] = (_status.get(source.key) or {}).get("last_error", "") if info["ok"] else info["error"]
            _status[source.key] = info
        _last.update(finished=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), new=saved["new"])
        after = await _after_save(saved, conf)
        _last.update(gist=after["gist"], rag=after["rag"])
        return {"started": True, "reason": "", "new": saved["new"], "updated": saved["updated"], "pruned": saved["pruned"], "after": after,
                "failed": sum(1 for _, _, i in results if not i["ok"])}
    finally:
        _running.release()


TICK_SECONDS = 60


def due_keys(listing: list[sources.Source], last_fetch: dict[str, float], now: float, default_minutes: int, limit: int = sources.MAX_PER_RUN) -> list[str]:
    """間隔の来たソースの source_key（最後に取ってからの経過が、そのソースの間隔以上。まだ取っていないものも含む）。
    間隔が来てから長く待たされているものを先に、1 回に limit 件まで。"""
    due = []
    for s in listing:
        every = (s.interval or default_minutes) * 60
        last = last_fetch.get(s.key)
        if last is None or now - last >= every:
            due.append((float("-inf") if last is None else last - every, s.key))
    due.sort()
    return [key for _, key in due[:limit]]


async def news_loop() -> None:
    """起動の少し後から、1 分ごとに見て、取得の間隔（ソースごと。無ければ設定の既定）が来たソースだけ取り（1 回 MAX_PER_RUN 件まで・同時 CONCURRENCY 件）（取得しない時間帯は除く）、日刊・週刊のまとめの時刻が来ていれば作る。
    テストでは動かさない（CONTROL_DECK_NEWS_AUTO=0）。"""
    from app.news import digest

    await asyncio.sleep(FIRST_DELAY_SECONDS)
    while True:
        if os.environ.get("CONTROL_DECK_NEWS_AUTO") != "0":
            try:      # ニュースハブとの同期・仕事の貸し出し（失敗しても自分の取得は止めない）
                await hub_sync.tick()
            except Exception:  # noqa: BLE001
                logger.exception("ニュースハブの見張りに失敗しました")
            try:
                conf = settings.load()
                if hub_sync.effective_source(conf) == "hub":      # 出どころがハブのあいだは、自分では集めない（auto は答えなくなったらここを通って集め始める）
                    pass
                elif not settings.in_quiet(conf, datetime.now()):
                    listing = sources.build_sources(sources.load_config(), channels.load()["channels"])
                    due = due_keys(listing, _last_fetch, time.monotonic(), channels.load()["interval_minutes"])
                    if due:
                        result = await refresh(force=True, keys=due)
                        if result.get("started"):
                            logger.info("ニュースを取得しました: %s 件のソース・新着 %s 件・失敗 %s 件", len(due), result["new"], result["failed"])
            except Exception:  # noqa: BLE001 - 1 回の失敗で見張りを止めない
                logger.exception("ニュースの取得に失敗しました")
            try:
                for row in await digest.run_due():
                    logger.info("ニュースのまとめを作りました: %s %s", row.get("period"), row.get("day"))
            except Exception:  # noqa: BLE001
                logger.exception("ニュースのまとめを作れませんでした")
        await asyncio.sleep(TICK_SECONDS)
