"""SearXNG 連携ヘルパーのテスト。"""
import asyncio


def _reset_cache():
    from app.workflows import searxng

    searxng._url_cache = (0.0, "")


def test_resolve_url_defaults_to_local():
    from app.workflows.searxng import DEFAULT_URL, resolve_url

    _reset_cache()
    # 未登録環境 → ハードコード既定へフォールバック
    assert asyncio.run(resolve_url("")) == DEFAULT_URL
    assert asyncio.run(resolve_url(None)) == DEFAULT_URL
    # 明示 URL はそのまま（自動検出より優先）
    assert asyncio.run(resolve_url("  http://192.168.1.5:8888/  ")) == "http://192.168.1.5:8888"


def test_default_url_autodetects_registered_app(client):
    """管理アプリ「SearXNG」の web_port から既定 URL を自動検出する。

    web.search / チャット検索 / Deep Research / warmup は全て resolve_url("") を
    通るため、ここが正しければ呼び出し経路間で URL の不整合は起きない。
    """
    from app.database import SessionLocal
    from app.models import ManagedApplication
    from app.workflows import searxng

    db = SessionLocal()
    try:
        app = ManagedApplication(
            name="SearXNG", application_type="shell_script",
            script_path="/tmp/run.sh", web_port=8890,
        )
        db.add(app)
        db.commit()
        app_id = app.id
    finally:
        db.close()
    try:
        _reset_cache()
        assert searxng.default_url_sync() == "http://127.0.0.1:8890"
        assert asyncio.run(searxng.resolve_url("")) == "http://127.0.0.1:8890"
        # TTL キャッシュが効く（DB を消しても直後は同じ値）
        assert searxng.default_url_sync() == "http://127.0.0.1:8890"
    finally:
        db = SessionLocal()
        try:
            row = db.get(ManagedApplication, app_id)
            if row is not None:
                db.delete(row)
                db.commit()
        finally:
            db.close()
        _reset_cache()


def test_is_local_detection():
    from app.workflows.searxng import _is_local

    assert _is_local("http://127.0.0.1:8888")
    assert _is_local("http://localhost:8888")
    assert not _is_local("https://searx.example.org")


def test_ensure_running_noop_for_remote_and_unregistered():
    """リモート URL は何もしない。未登録環境でも例外を出さない（検索側のエラーに任せる）。"""
    from app.workflows.searxng import ensure_running

    asyncio.run(ensure_running("https://searx.example.org"))  # リモート → 即 return
    # ローカル & 未登録（テスト DB に SearXNG アプリなし）→ ログのみで正常終了
    asyncio.run(ensure_running("http://127.0.0.1:59999"))


def test_idle_check_only_stops_when_started_by_us(monkeypatch):
    """自動起動分のみアイドル停止。手動起動・直近利用は停止しない。"""
    import time

    from app.workflows import searxng

    async def alive_true(url):
        return True

    stopped = {"n": 0}
    monkeypatch.setattr(searxng, "_alive", alive_true)
    monkeypatch.setattr(searxng, "_stop_registered_app", lambda: stopped.__setitem__("n", stopped["n"] + 1))

    # 手動起動扱い（フラグなし）→ 何もしない
    monkeypatch.setattr(searxng, "_started_by_us", False)
    monkeypatch.setattr(searxng, "_last_used", 0.0)
    assert asyncio.run(searxng._idle_check_once()) is False

    # 自動起動 + 直近利用 → 停止しない
    monkeypatch.setattr(searxng, "_started_by_us", True)
    monkeypatch.setattr(searxng, "_last_used", time.time())
    assert asyncio.run(searxng._idle_check_once()) is False
    assert stopped["n"] == 0

    # 自動起動 + アイドル閾値超過 → 停止してフラグ解除
    monkeypatch.setattr(searxng, "_last_used", time.time() - searxng.IDLE_STOP_MINUTES * 60 - 1)
    assert asyncio.run(searxng._idle_check_once()) is True
    assert stopped["n"] == 1
    assert searxng._started_by_us is False
