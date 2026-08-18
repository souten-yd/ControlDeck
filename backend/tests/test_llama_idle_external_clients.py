"""Control Deckを経由しない利用（OpenCode等）でllama instanceを止めない。"""
from __future__ import annotations


def test_connected_client_blocks_idle_unload(monkeypatch):
    from app.models_mgmt import llama

    class Connection:
        status = "ESTABLISHED"

        class laddr:  # noqa: N801 - psutilのnamedtuple互換
            port = 8090

    class FakePsutil:
        CONN_ESTABLISHED = "ESTABLISHED"

        @staticmethod
        def net_connections(kind="tcp"):
            return [Connection()]

    monkeypatch.setitem(__import__("sys").modules, "psutil", FakePsutil)
    assert llama._has_connected_clients(8090) is True
    assert llama._has_connected_clients(8091) is False


def _sessions(monkeypatch, rows):
    class Terminals:
        @staticmethod
        def list_sessions():
            return rows

    monkeypatch.setattr("app.terminals.manager.manager", Terminals)


def test_recently_used_opencode_session_marks_endpoint_in_use(monkeypatch):
    import time

    from app.models_mgmt import llama

    monkeypatch.setattr("app.features.registry.is_enabled", lambda feature_id: feature_id == "opencode")
    monkeypatch.setattr(
        "app.integrations.opencode.provider.get_settings",
        lambda: {"base_url": "http://127.0.0.1:8090/v1", "model": "llama", "project_path": ""},
    )
    now = time.time()

    _sessions(monkeypatch, [
        {"id": "a", "program": "bash", "alive": True, "activity_at": now},
        {"id": "b", "program": "opencode.exe", "alive": True, "attached": False, "activity_at": now - 60},
    ])
    assert llama._opencode_session_uses(8090, window_seconds=1800) is True
    # 別portのinstanceは対象外。
    assert llama._opencode_session_uses(8091, window_seconds=1800) is False
    # 見ていないセッションのために起動し直さない。
    assert llama._opencode_session_uses(8090, window_seconds=1800, require_attached=True) is False

    # 放置されたTUIは保持しない（idle窓を超えた活動時刻）。
    _sessions(monkeypatch, [
        {"id": "b", "program": "opencode.exe", "alive": True, "attached": True, "activity_at": now - 4000},
    ])
    assert llama._opencode_session_uses(8090, window_seconds=1800) is False

    _sessions(monkeypatch, [{"id": "a", "program": "bash", "alive": True, "activity_at": now}])
    assert llama._opencode_session_uses(8090, window_seconds=1800) is False


def test_disabled_feature_does_not_hold_instances(monkeypatch):
    from app.models_mgmt import llama

    monkeypatch.setattr("app.features.registry.is_enabled", lambda feature_id: False)
    assert llama._opencode_session_uses(8090, window_seconds=1800) is False


def test_revive_skips_when_opencode_goes_through_the_gateway(monkeypatch):
    """ゲートウェイ経由ならリクエスト時に起きるので、使っていない間は起こし直さない。"""
    import asyncio

    from app.models_mgmt import llama

    monkeypatch.setattr("app.features.registry.is_enabled", lambda feature_id: True)
    monkeypatch.setattr(
        "app.integrations.opencode.provider.get_settings",
        lambda: {"base_url": "http://127.0.0.1:8765/api/v1/llm/v1", "model": "auto",
                 "project_path": "", "use_gateway": True},
    )

    def _fail(*args, **kwargs):
        raise AssertionError("ゲートウェイ経由では起動判定まで進まない")

    monkeypatch.setattr(llama, "_opencode_session_uses", _fail)
    asyncio.run(llama._revive_endpoint_for_opencode(1800))


def test_revive_starts_the_resolved_port_when_connected_directly(monkeypatch):
    """直結設定では、attachされたセッションの転送先portを起こす。"""
    import asyncio

    from app.models_mgmt import llama

    monkeypatch.setattr("app.features.registry.is_enabled", lambda feature_id: True)
    monkeypatch.setattr(
        "app.integrations.opencode.provider.get_settings",
        lambda: {"base_url": "http://127.0.0.1:8090/v1", "model": "llama",
                 "project_path": "", "use_gateway": False},
    )
    monkeypatch.setattr("app.integrations.opencode.provider.resolve_backend_port", lambda: 8090)
    monkeypatch.setattr(llama, "_opencode_session_uses", lambda *a, **k: True)
    monkeypatch.setattr(llama, "list_instances", lambda: [
        {"alias": "llama", "role": "llm", "port": 8090, "loaded": False},
    ])
    started: list[str] = []

    async def _ensure(base_url, **kwargs):
        started.append(base_url)
        return True

    monkeypatch.setattr(llama, "ensure_ready_by_base_url", _ensure)
    asyncio.run(llama._revive_endpoint_for_opencode(1800))
    assert started == ["http://127.0.0.1:8090/v1"]
