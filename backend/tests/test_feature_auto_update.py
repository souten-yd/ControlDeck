"""オプション機能の自動更新（1 日 1 回、決めた時刻に）。

目的: 時刻を過ぎたらその日に 1 回だけ、使われていないときに更新すること。対象は ControlDeck が導入した
壊れていない機能だけで、1 つの失敗で残りを止めないこと。根拠: 2026-11-20 利用者の依頼
「オプション機能を 1 日 1 回、時間を決めて自動でバージョンアップ。ボタンと時間設定を付けて」。
"""
import asyncio
from datetime import datetime

import pytest

from app.features import auto_update


@pytest.fixture
def env(monkeypatch, tmp_path):
    monkeypatch.setattr(auto_update, "data_dir", lambda: tmp_path)
    started: list[str] = []
    monkeypatch.setattr(auto_update, "start_job", lambda *a, trigger="manual", **k: started.append(trigger))
    monkeypatch.setattr(auto_update, "running", lambda: False)
    busy: list[str] = []
    from app.maintenance import auto_update as core, self_update

    monkeypatch.setattr(core, "busy_reasons", lambda: list(busy))
    monkeypatch.setattr(self_update, "_saved_state", lambda: {})
    return {"started": started, "busy": busy}


def at(hhmm: str, day: int = 20) -> datetime:
    hour, minute = map(int, hhmm.split(":"))
    return datetime(2026, 11, day, hour, minute)


def test_off_by_default_and_time_is_validated(env):
    assert auto_update.get_settings() == {"enabled": False, "time": "04:00"}
    assert not asyncio.run(auto_update.tick(at("12:00")))
    with pytest.raises(ValueError):
        auto_update.save_settings({"time": "24:00"})
    assert auto_update.save_settings({"enabled": True, "time": "03:30"}) == {"enabled": True, "time": "03:30"}


def test_runs_once_a_day_after_the_time(env):
    auto_update.save_settings({"enabled": True, "time": "03:30"})
    assert not asyncio.run(auto_update.tick(at("03:29")))
    assert asyncio.run(auto_update.tick(at("03:30")))
    assert not asyncio.run(auto_update.tick(at("10:00"))), "同じ日に 2 回走った"
    assert asyncio.run(auto_update.tick(at("03:31", day=21)))
    assert env["started"] == ["schedule", "schedule"]


def test_waits_while_in_use(env):
    auto_update.save_settings({"enabled": True, "time": "03:00"})
    env["busy"].append("LLM の推論中")
    assert not asyncio.run(auto_update.tick(at("03:05")))
    env["busy"].clear()
    assert asyncio.run(auto_update.tick(at("05:40"))), "空いたら同じ日のうちに走る"


def test_updates_only_managed_healthy_features_and_keeps_going(monkeypatch, env):
    from app.features import gpu_runtime, registry

    items = [
        {"id": "opencode-v2", "name": "OpenCode", "kind": "npm", "installed": True, "managed": True, "version": "1.0"},
        {"id": "broken", "name": "壊れ", "kind": "npm", "installed": True, "managed": True, "version": "1", "error": "x"},
        {"id": "external", "name": "外部", "kind": "npm", "installed": True, "managed": False, "version": "1"},
        {"id": "supertonic", "name": "音声", "kind": "speech-engine", "installed": True, "managed": True},
        {"id": "llama-cpp", "name": "llama.cpp", "kind": "gpu-runtime", "installed": True, "managed": True, "version": "b1"},
        {"id": "strata", "name": "Strata", "kind": "gpu-runtime", "installed": True, "managed": True, "version": "v1"},
        {"id": "plugin", "name": "拡張", "kind": "npm", "installed": True, "managed": True, "version": "2.0"},
    ]
    monkeypatch.setattr(registry, "list_features", lambda: [dict(i) for i in items])
    called: list[str] = []

    def update(feature_id):
        called.append(feature_id)
        if feature_id == "opencode-v2":
            raise registry.FeatureError("npm に届きません")
        return {"version": "2.0"}

    async def release_status(feature_id):
        return {"update_available": feature_id == "llama-cpp", "error": ""}

    async def gpu_update(feature_id, job=None):
        called.append(feature_id)
        return {"version": "b2"}

    monkeypatch.setattr(registry, "update", update)
    monkeypatch.setattr(gpu_runtime, "release_status", release_status)
    monkeypatch.setattr(gpu_runtime, "update", gpu_update)
    sent: list[str] = []

    async def notify(title, message):
        sent.append(title)

    from app.maintenance import auto_update as core

    monkeypatch.setattr(core, "notify", notify)
    result = asyncio.run(auto_update.run_all(trigger="manual"))
    assert called == ["opencode-v2", "llama-cpp", "plugin"], "対象外を更新した / 新版の無い GPU ランタイムを更新した"
    status = {r["id"]: r["status"] for r in result["results"]}
    assert status == {"opencode-v2": "failed", "llama-cpp": "updated", "strata": "current", "plugin": "current"}
    assert auto_update.get_state()["finished_at"]
    assert sent == ["オプション機能の更新で失敗がありました"]


def test_api_routes_are_not_taken_by_feature_actions(admin_client, monkeypatch, tmp_path):
    """/features/auto-update は /{feature_id}/{action} に取られない（固定のパスを先に置く）。"""
    monkeypatch.setattr(auto_update, "data_dir", lambda: tmp_path)
    headers = {"X-Requested-With": "ControlDeck"}
    r = admin_client.get("/api/v1/features/auto-update")
    assert r.status_code == 200 and r.json()["enabled"] is False
    r = admin_client.put("/api/v1/features/auto-update", json={"enabled": True, "time": "05:15"}, headers=headers)
    assert r.status_code == 200 and r.json()["time"] == "05:15"
    assert admin_client.put("/api/v1/features/auto-update", json={"time": "5:15"}, headers=headers).status_code == 422
    monkeypatch.setattr(auto_update, "start_job", lambda *a, **k: type("J", (), {"id": "j1"})())
    monkeypatch.setattr(auto_update, "running", lambda: False)
    r = admin_client.post("/api/v1/features/auto-update/run-jobs", headers=headers)
    assert r.status_code == 201 and r.json() == {"job_id": "j1"}
