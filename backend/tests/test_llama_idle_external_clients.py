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
