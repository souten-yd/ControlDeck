"""取得の実行（同時に 1 回だけ・ソースごとに時間切れと失敗を記録して他を止めない）と、30 分ごとの見張り。"""
from __future__ import annotations

import asyncio
import logging
import os
import threading
import time
from datetime import datetime, timezone

from app.news import channels, sources, store

logger = logging.getLogger("control_deck.news.service")

INTERVAL_SECONDS = 30 * 60
FIRST_DELAY_SECONDS = 60
SOURCE_TIMEOUT_SECONDS = 45
MIN_GAP_SECONDS = 30          # 「取り直す」の連打を抑える
CONCURRENCY = 4

_running = threading.Lock()
_status: dict[str, dict] = {}
_last = {"started": 0.0, "finished": None, "new": 0}


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


def _save(items: list[dict]) -> dict:
    from app.database import SessionLocal

    with SessionLocal() as db:
        result = store.upsert_items(db, items) if items else {"new": 0, "updated": 0}
        result["pruned"] = store.prune(db, channels.load()["archive_keep"])
        return result


async def refresh(*, force: bool = False) -> dict:
    """全ソースを取り直す。実行中・直前に取ったばかりなら取らない（force で間隔だけ無視）。
    {"started": bool, "reason": ..., "new": 件数, "failed": 失敗したソース数}。"""
    if not _running.acquire(blocking=False):
        return {"started": False, "reason": "running"}
    try:
        if not force and time.monotonic() - _last["started"] < MIN_GAP_SECONDS:
            return {"started": False, "reason": "recent"}
        _last["started"] = time.monotonic()
        await asyncio.to_thread(store.ensure_fts)
        cfg = sources.load_config()
        listing = sources.build_sources(cfg, channels.load()["channels"])
        gate = asyncio.Semaphore(CONCURRENCY)
        results = await asyncio.gather(*[_fetch_one(s, gate) for s in listing])
        items = [item for _, got, _ in results for item in got]
        saved = await asyncio.to_thread(_save, items)
        # 設定から外したソースの状態は残さない
        keep = {s.key for s in listing}
        for key in list(_status):
            if key not in keep:
                _status.pop(key)
        for source, _, info in results:
            _status[source.key] = info
        _last.update(finished=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), new=saved["new"])
        return {"started": True, "reason": "", "new": saved["new"], "updated": saved["updated"], "pruned": saved["pruned"],
                "failed": sum(1 for _, _, i in results if not i["ok"])}
    finally:
        _running.release()


async def news_loop() -> None:
    """起動の少し後に 1 回、その後 30 分ごとに取る。テストでは動かさない（CONTROL_DECK_NEWS_AUTO=0）。"""
    await asyncio.sleep(FIRST_DELAY_SECONDS)
    while True:
        if os.environ.get("CONTROL_DECK_NEWS_AUTO") != "0":
            try:
                result = await refresh(force=True)
                if result.get("started"):
                    logger.info("ニュースを取得しました: 新着 %s 件・失敗 %s 件", result["new"], result["failed"])
            except Exception:  # noqa: BLE001 - 1 回の失敗で見張りを止めない
                logger.exception("ニュースの取得に失敗しました")
        await asyncio.sleep(channels.load()["interval_minutes"] * 60)
