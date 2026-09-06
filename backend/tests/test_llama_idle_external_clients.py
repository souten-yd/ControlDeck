"""Control Deckを経由しない利用（OpenCode等）でllama instanceを止めない。"""
from __future__ import annotations

import pytest


def test_connected_client_blocks_idle_unload(monkeypatch):
    """外部 client が繋いでいる間は降ろさない。

    見るのは client 側の socket である。server 側（laddr.port == port）の pid は
    常に llama 自身で、誰が繋いでいるかを何も語らない。
    """
    import os

    from app.models_mgmt import llama

    class Connection:
        status = "ESTABLISHED"
        pid = os.getpid() + 99999  # 外部プロセス

        class laddr:  # noqa: N801 - psutilのnamedtuple互換
            port = 40001

        class raddr:  # noqa: N801 - psutilのnamedtuple互換
            port = 8090

    class FakePsutil:
        CONN_ESTABLISHED = "ESTABLISHED"

        class Error(Exception):
            pass

        class Process:
            def __init__(self, *args):
                pass

            def children(self, recursive=False):
                return []

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


def test_auto_restart_loop_is_reported_as_failed(monkeypatch):
    """起動失敗の再試行ループを「起動中」と見せない。

    モデル読込中と区別が付かないと、UIが延々と「読み込み待ち」を出し続ける。
    """
    from app.models_mgmt import llama

    monkeypatch.setattr(llama, "get_config", lambda: {
        "instances": {"broken": {"port": 8096, "role": "llm", "order": 1}},
        "selected_alias": "broken", "endpoints": {},
    })
    monkeypatch.setattr("app.applications.systemd.query_status",
                        lambda unit, **kwargs: {"status": "STARTING", "sub_state": "auto-restart"})
    monkeypatch.setattr(llama, "_log_tail", lambda alias, max_chars=300: "failed to create MTP context")

    item = llama.list_instances()[0]
    assert item["runtime_status"] == "FAILED"
    assert item["loaded"] is False
    assert "MTP" in item["last_error"]


def test_loading_model_is_still_reported_as_starting(monkeypatch):
    """通常の読み込み中（再試行ループではない）は従来どおり起動中として扱う。"""
    from app.models_mgmt import llama

    monkeypatch.setattr(llama, "get_config", lambda: {
        "instances": {"loading": {"port": 8096, "role": "llm", "order": 1}},
        "selected_alias": "loading", "endpoints": {},
    })
    monkeypatch.setattr("app.applications.systemd.query_status",
                        lambda unit, **kwargs: {"status": "STARTING", "sub_state": "start"})

    item = llama.list_instances()[0]
    assert item["runtime_status"] == "STARTING"
    assert item["loaded"] is True
    assert item["last_error"] == ""


def test_throughput_is_measured_from_the_cumulative_counter(monkeypatch):
    """全slot合算の tok/s は累計トークンの差分から出す。

    llama.cpp の predicted_tokens_seconds は直近1リクエストの速度なので、
    並列で回したときに全体でどれだけ出ているかが分からない。
    """
    from app.models_mgmt import llama

    clock = {"now": 100.0}
    monkeypatch.setattr("time.monotonic", lambda: clock["now"])
    llama._THROUGHPUT_SAMPLES.pop(9999, None)

    # 初回は基準を取るだけ
    assert llama._throughput(9999, 1000.0) == 0.0
    # 4秒で200トークン → 50 tok/s
    clock["now"] = 104.0
    assert llama._throughput(9999, 1200.0) == 50.0
    # 画面を複数開いて間隔が詰まっても、窓の最古の点と比べるので計算できる
    clock["now"] = 104.5
    assert llama._throughput(9999, 1210.0) == pytest.approx(1210 / 4.5 - 1000 / 4.5)
    # 窓を越えて見に来なかっただけでは基準を捨てない。捨てると手元に1点しか
    # 残らず 0 を返すしかなくなり、見に来る間隔が窓より広い相手には生成中でも
    # ずっと 0 が出る（携帯の回線や tunnel 越しで実際にそうなった）。
    # 13 秒で 400 トークン。
    clock["now"] = 113.0
    assert llama._throughput(9999, 1400.0) == pytest.approx(400 / 13)
    clock["now"] = 116.0
    assert llama._throughput(9999, 1550.0) == pytest.approx(550 / 16)
    # 止まっていた時間が長すぎるときだけ測り直す。そこまで含めて平均すると、
    # 再開直後の速度が実態よりずっと低く出るため。
    clock["now"] = 116.0 + llama.THROUGHPUT_STALE_SECONDS + 1
    assert llama._throughput(9999, 1550.0) == 0.0
    clock["now"] += 3.0
    assert llama._throughput(9999, 1700.0) == 50.0
    # サーバー再起動で大きく巻き戻ったら基準を取り直す
    clock["now"] = 118.0
    assert llama._throughput(9999, 5.0) == 0.0
