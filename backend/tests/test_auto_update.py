"""本体の自動適用（L1）: 更新があり、使われていない時間が続いたときだけ始める。"""
import asyncio

import pytest

from app.maintenance import auto_update, self_update


@pytest.fixture
def env(monkeypatch, tmp_path):
    monkeypatch.setattr(auto_update, "data_dir", lambda: tmp_path)
    state = {"status": {"update_available": True, "remote_sha": "a" * 40, "release_tag": "ControlDeck_v0.13",
                        "error": "", "job": {}},
             "busy": [], "started": [], "notified": []}
    monkeypatch.setattr(self_update, "status", lambda **_: dict(state["status"]))
    monkeypatch.setattr(self_update, "start", lambda sha, **kw: state["started"].append((sha, kw)) or {})
    monkeypatch.setattr(auto_update, "busy_reasons", lambda: list(state["busy"]))

    async def notify(title, message):
        state["notified"].append(title)

    monkeypatch.setattr(auto_update, "notify", notify)
    return state


def run(coro):
    return asyncio.run(coro)


def test_waits_for_the_idle_period_then_starts(env):
    since = run(auto_update.tick(None, 1000.0))
    assert since == 1000.0 and env["started"] == []
    assert run(auto_update.tick(since, 1000.0 + 9 * 60)) == 1000.0
    assert env["started"] == []
    assert run(auto_update.tick(since, 1000.0 + 10 * 60)) is None
    assert env["started"] == [("a" * 40, {"automatic": True, "release_tag": "ControlDeck_v0.13"})]
    assert env["notified"] == ["ControlDeck を自動更新します"]


def test_activity_resets_the_idle_clock(env):
    since = run(auto_update.tick(None, 0.0))
    env["busy"] = ["LLM の推論中"]
    assert run(auto_update.tick(since, 11 * 60)) is None
    env["busy"] = []
    assert run(auto_update.tick(None, 12 * 60)) == 12 * 60
    assert env["started"] == []


@pytest.mark.parametrize("patch", [
    {"update_available": False}, {"error": "mainブランチでのみ更新できます"}, {"job": {"phase": "running"}},
])
def test_nothing_to_do(env, patch):
    env["status"].update(patch)
    assert run(auto_update.tick(0.0, 3600.0)) is None
    assert env["started"] == []


def test_can_be_turned_off(env):
    auto_update.save_settings({"enabled": False})
    assert run(auto_update.tick(0.0, 3600.0)) is None
    assert env["started"] == []
    assert auto_update.save_settings({"enabled": True, "idle_minutes": 0})["idle_minutes"] == 1


def test_result_is_reported_once(env, monkeypatch):
    saved = {"phase": "failed", "automatic": True, "error": "更新後の画面が配信されません"}
    monkeypatch.setattr(self_update, "_saved_state", lambda: dict(saved))
    monkeypatch.setattr(self_update, "_write_state", lambda value: saved.update(value))
    run(auto_update.report_result_once())
    run(auto_update.report_result_once())
    assert env["notified"] == ["ControlDeck の自動更新に失敗しました"]
    assert saved["reported"] is True


def test_manual_updates_are_not_reported_as_automatic(env, monkeypatch):
    monkeypatch.setattr(self_update, "_saved_state", lambda: {"phase": "succeeded"})
    run(auto_update.report_result_once())
    assert env["notified"] == []


def test_busy_reasons_cover_open_opencode_sessions(monkeypatch):
    from app.terminals import manager as terminal_manager

    monkeypatch.setattr(terminal_manager.manager, "list_sessions", lambda: [{"program": "opencode2"}])
    assert "OpenCode のセッションが開いている" in auto_update.busy_reasons()
