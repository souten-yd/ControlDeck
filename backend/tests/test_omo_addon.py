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


@pytest.mark.parametrize(("slots", "expected"), [(1, 1), (2, 1), (4, 3), (8, 7)])
def test_direct_connection_leaves_a_slot_for_the_interactive_agent(slots, expected):
    """llama.cpp 直結なら誰も待たせてくれないので、メイン用に1本空ける。"""
    assert provider.omo_concurrency_for(slots, gated=False) == (expected, expected)


@pytest.mark.parametrize("slots", [1, 4, 8])
def test_gateway_keeps_omo_defaults_regardless_of_slots(slots):
    """ゲートウェイ経由なら溢れを ControlDeck が待たせるので、slot数に縛らない。

    エージェントは常にLLMを呼ぶわけではないため、論理並列 > slot数 は
    健全なオーバーサブスクリプションになる（GPUを遊ばせにくい）。
    """
    assert provider.omo_concurrency_for(slots, gated=True) == (
        provider.OMO_DEFAULT_CONCURRENCY, provider.OMO_DEFAULT_TEAM_PARALLEL,
    )


def test_sync_writes_current_schema_and_keeps_user_overrides(tmp_path, monkeypatch):
    """現行スキーマ task.* へ書く。schema は strict なので旧キーを混ぜない。"""
    config = tmp_path / "omo.jsonc"
    config.write_text(json.dumps({
        "task": {"default_concurrency": 1,
                 "provider_concurrency": {"anthropic": 2},
                 "team": {"max_members": 8}},
        "other_setting": "keep me",
    }), encoding="utf-8")
    monkeypatch.setattr(provider, "_omo_config_path", lambda: config)
    monkeypatch.setattr(provider, "_settings_path", lambda: tmp_path / "s.json")
    provider.save_settings({"base_url": provider.gateway_base_url(), "model": "m",
                            "use_gateway": True})

    result = provider.sync_omo_concurrency(4)
    assert result["updated"] is True
    assert result["gated"] is True
    assert result["concurrency"] == provider.OMO_DEFAULT_CONCURRENCY

    saved = json.loads(config.read_text(encoding="utf-8"))
    assert saved["task"]["default_concurrency"] == 5
    assert saved["task"]["team"]["max_parallel_members"] == 4
    # 利用者の個別設定は残す（OMoは provider/model 別が上位）
    assert saved["task"]["provider_concurrency"] == {"anthropic": 2}
    assert saved["task"]["team"]["max_members"] == 8
    assert saved["other_setting"] == "keep me"
    assert "background_task" not in saved

    assert provider.sync_omo_concurrency(4)["updated"] is False


def test_sync_is_conservative_when_bypassing_the_gateway(tmp_path, monkeypatch):
    config = tmp_path / "omo.jsonc"
    monkeypatch.setattr(provider, "_omo_config_path", lambda: config)
    monkeypatch.setattr(provider, "_settings_path", lambda: tmp_path / "s.json")
    provider.save_settings({"base_url": "http://127.0.0.1:8090/v1", "model": "m",
                            "use_gateway": False})
    result = provider.sync_omo_concurrency(4)
    assert result["gated"] is False
    assert result["concurrency"] == 3  # メイン1本ぶんを空ける


def test_jsonc_comments_do_not_break_reading(tmp_path, monkeypatch):
    """利用者が手で書いたコメント付き設定を壊さない。"""
    config = tmp_path / "omo.jsonc"
    config.write_text('{\n  // メモ\n  "task": {"max_depth": 2}\n}', encoding="utf-8")
    monkeypatch.setattr(provider, "_omo_config_path", lambda: config)
    monkeypatch.setattr(provider, "_settings_path", lambda: tmp_path / "s.json")
    provider.save_settings({"base_url": provider.gateway_base_url(), "model": "m",
                            "use_gateway": True})
    assert provider.sync_omo_concurrency(4)["updated"] is True
    saved = json.loads(config.read_text(encoding="utf-8"))
    assert saved["task"]["max_depth"] == 2  # 既存キーを失わない
    assert saved["task"]["default_concurrency"] == 5


def test_config_path_is_under_dot_omo():
    """探索先は ~/.omo/omo.jsonc（~/.config/opencode ではない）。"""
    assert provider._omo_config_path().parent.name == ".omo"


def test_sync_creates_config_when_missing(tmp_path, monkeypatch):
    config = tmp_path / "omo.jsonc"
    monkeypatch.setattr(provider, "_omo_config_path", lambda: config)
    monkeypatch.setattr(provider, "_settings_path", lambda: tmp_path / "s.json")
    provider.save_settings({"base_url": "http://127.0.0.1:8090/v1", "model": "m",
                            "use_gateway": False})
    assert provider.sync_omo_concurrency(2)["updated"] is True
    saved = json.loads(config.read_text(encoding="utf-8"))
    assert saved["task"]["default_concurrency"] == 1


def test_sync_falls_back_to_configured_model_slots(tmp_path, monkeypatch):
    from app.models_mgmt import llama

    config = tmp_path / "omo.json"
    monkeypatch.setattr(provider, "_omo_config_path", lambda: config)
    monkeypatch.setattr(provider, "_settings_path", lambda: tmp_path / "s.json")
    monkeypatch.setattr(llama, "list_instances", lambda: [
        {"alias": "other", "role": "llm", "n_parallel": 8, "port": 8091},
        {"alias": "used", "role": "llm", "n_parallel": 4, "port": 8090},
    ])
    provider.save_settings({"base_url": "http://127.0.0.1:8090/v1", "model": "used",
                            "use_gateway": False})
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
