"""SearXNG 連携ヘルパー。

deck.sh searxng で直接導入したローカルインスタンス（管理アプリ「SearXNG」)を
「基本停止・使う時だけ起動」で管理する:

- web.search / チャット検索 / Deep Research（engine=searxng）の呼び出し時に
  停止していれば即起動（コールドスタート 2〜3 秒）。
- アシスタント UI は SearXNG エンジン選択時にウォームアップを投げるため、
  実際の検索時にはほぼ起動済みになる。
- ControlDeck が自動起動した分は、アイドル IDLE_STOP_MINUTES 経過で自動停止する。
  ユーザーが Apps ページから手動起動した場合は対象外（勝手に止めない）。
- ControlDeck 終了時、自動起動した分は道連れに停止する。
"""
from __future__ import annotations

import asyncio
import logging
import os
import time
from urllib.parse import urlparse

import httpx

logger = logging.getLogger("control_deck.searxng")

DEFAULT_URL = "http://127.0.0.1:8888"
APP_NAME = "searxng"  # 管理アプリ名（小文字比較）
_START_WAIT_SECONDS = 25
IDLE_STOP_MINUTES = float(os.environ.get("CONTROL_DECK_SEARXNG_IDLE_MIN", "15"))

# 多重起動要求を防ぐ
_start_lock = asyncio.Lock()
# 最終利用時刻と「ControlDeck が起動した」フラグ（手動起動分は自動停止しない）
_last_used: float = 0.0
_started_by_us: bool = False


# 自動検出した既定 URL のキャッシュ（DB 参照を検索のたびにしないため）
_url_cache: tuple[float, str] = (0.0, "")
_URL_CACHE_TTL = 60.0


def _registered_port() -> int | None:
    from app.database import SessionLocal

    db = SessionLocal()
    try:
        app = _find_app(db)
        return int(app.web_port) if app is not None and app.web_port else None
    finally:
        db.close()


def default_url_sync() -> str:
    """既定 URL を自動検出する。

    登録済み管理アプリ「SearXNG」の web_port（deck.sh searxng が登録、
    SEARXNG_PORT 変更にも追従）を正とし、未登録時のみ DEFAULT_URL に落ちる。
    全呼び出し経路（web.search ノード / チャット検索 / Deep Research / warmup /
    アイドル停止）がこの関数を通ることで URL の不整合を防ぐ。
    """
    global _url_cache
    now = time.time()
    ts, cached = _url_cache
    if cached and now - ts < _URL_CACHE_TTL:
        return cached
    try:
        port = _registered_port()
    except Exception:  # DB 未初期化などでも検索自体は既定で続行
        port = None
    url = f"http://127.0.0.1:{port}" if port else DEFAULT_URL
    _url_cache = (now, url)
    return url


async def resolve_url(url: str | None) -> str:
    """URL 未指定なら自動検出したローカル既定インスタンスを使う。"""
    explicit = (url or "").strip().rstrip("/")
    if explicit:
        return explicit
    return await asyncio.to_thread(default_url_sync)


def _is_local(url: str) -> bool:
    host = urlparse(url).hostname or ""
    return host in ("127.0.0.1", "localhost", "::1")


async def _alive(url: str) -> bool:
    try:
        async with httpx.AsyncClient(timeout=2) as client:
            r = await client.get(url + "/healthz")
        return r.status_code < 500
    except httpx.HTTPError:
        return False


def _find_app(db):
    from app.models import ManagedApplication

    return next(
        (a for a in db.query(ManagedApplication).all() if a.name.strip().lower() == APP_NAME),
        None,
    )


def _start_registered_app() -> str | None:
    """管理アプリ SearXNG を systemd 経由で起動する。失敗理由 or None を返す。"""
    from app.applications import service as apps
    from app.applications import systemd as sd
    from app.database import SessionLocal

    db = SessionLocal()
    try:
        app = _find_app(db)
        if app is None:
            return "管理アプリ「SearXNG」が未登録です（./deck.sh searxng で導入できます）"
        if app.application_type != "systemd_service":
            if not app.systemd_unit_name:  # 外部登録などでユニット名が未設定の場合に補完
                app.systemd_unit_name = sd.unit_name_for(app.id)
                db.commit()
            apps.sync_unit(app)
            sd.reset_failed(app.systemd_unit_name)
        ok, err = sd.start(app.systemd_unit_name)
        return None if ok else f"SearXNG の起動に失敗: {err}"
    finally:
        db.close()


def _stop_registered_app() -> None:
    from app.applications import systemd as sd
    from app.database import SessionLocal

    db = SessionLocal()
    try:
        app = _find_app(db)
        if app is not None and app.systemd_unit_name:
            sd.stop(app.systemd_unit_name)
    finally:
        db.close()


async def lifecycle_stop() -> None:
    """ControlDeck 終了時に呼ぶ。自動起動した分だけ道連れに停止する。"""
    if not _started_by_us:
        return
    try:
        await asyncio.to_thread(_stop_registered_app)
        logger.info("ControlDeck 終了に伴い SearXNG を停止しました")
    except Exception:
        logger.exception("SearXNG lifecycle_stop error")


async def ensure_running(url: str) -> None:
    """ローカル SearXNG が応答しなければ管理アプリを自動起動して待つ。

    リモート URL は対象外（そのまま返す）。起動できない場合も例外にはせず、
    後続の検索リクエスト側のエラーに任せる（メッセージが具体的なため）。
    """
    global _last_used, _started_by_us
    if not _is_local(url):
        return
    _last_used = time.time()
    if await _alive(url):
        return
    async with _start_lock:
        if await _alive(url):  # ロック待ちの間に起動済み
            return
        error = await asyncio.to_thread(_start_registered_app)
        if error:
            logger.info("SearXNG 自動起動せず: %s", error)
            return
        _started_by_us = True
        logger.info("SearXNG を自動起動しました（%s、アイドル %d 分で自動停止）", url, IDLE_STOP_MINUTES)
        deadline = asyncio.get_event_loop().time() + _START_WAIT_SECONDS
        while asyncio.get_event_loop().time() < deadline:
            await asyncio.sleep(1.0)
            if await _alive(url):
                return


async def _idle_check_once(now: float | None = None) -> bool:
    """ローカル SearXNG がアイドル閾値を超えていれば停止する。停止したら True。

    SearXNG はサーバー側完全管理（Apps からの手動起動経路なし）のため、
    起動元を問わず自動停止の対象とする。backend 再起動で利用時刻が失われた
    場合は、初回チェック時刻を起点にアイドル猶予を仕切り直す。
    """
    global _last_used, _started_by_us
    current = now or time.time()
    url = await asyncio.to_thread(default_url_sync)
    if not await _alive(url):  # 稼働していない
        _started_by_us = False
        return False
    if _last_used == 0.0:  # 再起動直後など利用時刻不明 → 猶予を仕切り直す
        _last_used = current
        return False
    if current - _last_used < IDLE_STOP_MINUTES * 60:
        return False
    await asyncio.to_thread(_stop_registered_app)
    _started_by_us = False
    logger.info("SearXNG をアイドル（%d 分）のため自動停止しました", IDLE_STOP_MINUTES)
    return True


async def idle_stop_loop() -> None:
    """自動起動した SearXNG をアイドル一定時間で停止する常駐ループ。"""
    while True:
        try:
            await asyncio.sleep(60)
            await _idle_check_once()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("searxng idle_stop_loop error")
