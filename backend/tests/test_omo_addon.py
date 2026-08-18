"""OMo（oh-my-openagent）アドオンと、モデル並列数への追従。

OMo は OpenCode プラグインとして背景タスクを並列実行する。モデル側のスロット数と
OMo が投げる本数がずれると、片方だけ変えたときに詰まるか遊ぶかのどちらかになる。
"""
from __future__ import annotations

import json

import pytest

from app.features import registry
from app.integrations.opencode import provider


def test_omo_is_registered_as_addon_requiring_opencode():
    assert "omo" in registry.KNOWN_FEATURES
    spec = registry.FEATURES["omo"]
    assert spec["package"] == "oh-my-openagent"
    assert spec["executable"] == "omo"
    # OpenCode のプラグインなので単体では動かない
    assert spec["requires"] == "opencode"


def test_install_requires_opencode_first(monkeypatch):
    monkeypatch.setattr(registry, "status",
                        lambda fid: {"installed": False} if fid == "opencode" else {"installed": True})
    with pytest.raises(registry.FeatureError, match="OpenCode"):
        registry.install("omo")


@pytest.mark.parametrize(("slots", "expected"), [
    (1, 1),   # 1本しかなくても背景を止めはしない
    (2, 1),
    (4, 3),   # メインエージェント用に1本空ける
    (8, 7),
])
def test_concurrency_leaves_a_slot_for_the_interactive_agent(slots, expected):
    assert provider.omo_concurrency_for(slots) == expected


def test_sync_writes_default_concurrency_and_keeps_user_overrides(tmp_path, monkeypatch):
    """既定値だけ書き、利用者が付けた個別上書きは残す。

    OMo は modelConcurrency > providerConcurrency > defaultConcurrency の順で
    解決するので、既定値を書いても個別指定は生きる。
    """
    config = tmp_path / "oh-my-openagent.json"
    config.write_text(json.dumps({
        "background_task": {"defaultConcurrency": 5, "providerConcurrency": {"anthropic": 2}},
        "other_setting": "keep me",
    }), encoding="utf-8")
    monkeypatch.setattr(provider, "_omo_config_path", lambda: config)

    result = provider.sync_omo_concurrency(4)
    assert result["updated"] is True
    assert result["concurrency"] == 3

    saved = json.loads(config.read_text(encoding="utf-8"))
    assert saved["background_task"]["defaultConcurrency"] == 3
    assert saved["background_task"]["providerConcurrency"] == {"anthropic": 2}
    assert saved["other_setting"] == "keep me"

    # 同じ値なら書き直さない
    assert provider.sync_omo_concurrency(4)["updated"] is False


def test_sync_creates_config_when_missing(tmp_path, monkeypatch):
    config = tmp_path / "new.json"
    monkeypatch.setattr(provider, "_omo_config_path", lambda: config)
    assert provider.sync_omo_concurrency(2)["updated"] is True
    assert json.loads(config.read_text(encoding="utf-8"))["background_task"]["defaultConcurrency"] == 1


def test_sync_falls_back_to_configured_model_slots(tmp_path, monkeypatch):
    from app.models_mgmt import llama

    config = tmp_path / "omo.json"
    monkeypatch.setattr(provider, "_omo_config_path", lambda: config)
    monkeypatch.setattr(provider, "_settings_path", lambda: tmp_path / "s.json")
    monkeypatch.setattr(llama, "list_instances", lambda: [
        {"alias": "other", "role": "llm", "n_parallel": 8, "port": 8091},
        {"alias": "used", "role": "llm", "n_parallel": 4, "port": 8090},
    ])
    provider.save_settings({"base_url": "http://127.0.0.1:8090/v1", "model": "used"})
    # 引数なしなら OpenCode が使っているモデルのスロット数を見る
    assert provider.sync_omo_concurrency()["concurrency"] == 3


def test_model_save_syncs_only_the_model_opencode_uses(tmp_path, monkeypatch):
    """関係ないモデルの設定変更で OMo をいじらない。"""
    from app.models_mgmt import llama

    monkeypatch.setattr(llama, "_config_path", lambda: tmp_path / "llama.json")
    monkeypatch.setattr(llama, "is_installed", lambda: False)
    monkeypatch.setattr(provider, "_settings_path", lambda: tmp_path / "s.json")
    monkeypatch.setattr(registry, "is_enabled", lambda fid: fid == "omo")
    provider.save_settings({"base_url": "http://127.0.0.1:8090/v1", "model": "used"})

    called: list[int] = []
    monkeypatch.setattr(provider, "sync_omo_concurrency",
                        lambda n=None: called.append(n) or {"updated": True})

    llama.save_instance("unrelated", {"alias": "unrelated", "model_path": "/m/u.gguf",
                                      "port": 9800, "n_parallel": 8})
    assert called == [], "OpenCodeが使っていないモデルでは同期しない"

    llama.save_instance("used", {"alias": "used", "model_path": "/m/a.gguf",
                                 "port": 9801, "n_parallel": 4})
    assert called == [4], "使用中モデルのスロット数変更には追従する"
