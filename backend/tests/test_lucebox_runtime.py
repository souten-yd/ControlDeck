"""Lucebox ランタイム管理（DL/GPU 不要のロジック部分）。"""
import asyncio
import json

import pytest


@pytest.fixture()
def lucebox(monkeypatch, tmp_path):
    from app.models_mgmt import lucebox as module

    monkeypatch.setattr(module, "_config_path", lambda: tmp_path / "lucebox-runtime.json")
    monkeypatch.setattr(module, "runtimes_dir", lambda: tmp_path / "runtimes")
    monkeypatch.setattr(module, "_runtime_state", lambda _alias: {"status": "STOPPED"})
    monkeypatch.setattr(module, "_sync_auto_start", lambda _alias: None)
    return module


def test_track_asset_patterns_pick_the_right_rocm_build(lucebox):
    from app.models_mgmt import gpu_release

    assets = [
        {"name": "lucebox-r9700-rocm10.0.0-gfx1201-298031aa.tar.zst"},
        {"name": "lucebox-r9700-rocm7.2.4-gfx1201-298031aa.tar.zst"},
        {"name": "production-acceptance-lucebox-298031aa-r1.tar.gz"},
        {"name": "SHA256SUMS"},
    ]
    rocm10 = gpu_release.pick_asset(assets, lucebox.TRACKS["rocm10"]["pattern"])
    rocm7 = gpu_release.pick_asset(assets, lucebox.TRACKS["rocm7"]["pattern"])
    assert rocm10["name"].startswith("lucebox-r9700-rocm10")
    assert rocm7["name"].startswith("lucebox-r9700-rocm7")
    # 初期トラックは ROCm 10（本番候補ビルド）。
    assert lucebox.DEFAULT_TRACK == "rocm10"


def test_defaults_follow_the_published_measured_profile(lucebox):
    """推奨設定をそのまま初期値にする（AMDLucebox READMEの実測プロファイル）。"""
    defaults = lucebox.DEFAULT_INSTANCE
    assert defaults["draft_block_size"] == 16
    assert defaults["max_ctx"] == 131072
    assert defaults["cache_type_k"] == "q8_0" and defaults["cache_type_v"] == "q8_0"
    assert defaults["port"] == 8216
    assert defaults["ddtree"] is True
    # AMDLucebox の起動コマンドは --fa-window を渡さない（= dflash_server 既定の 0）。
    # >0 は長コンテキストでツール定義を注意から落とすため、既定にしてはいけない。
    assert defaults["fa_window"] == 0


def test_save_instance_roundtrip_and_defaults(lucebox):
    saved = lucebox.save_instance("luce", {"model_path": "/m/target.gguf",
                                           "draft_path": "/m/draft.gguf"})
    assert saved["max_ctx"] == 131072 and saved["cache_type_k"] == "q8_0"
    assert lucebox.get_config()["selected_alias"] == "luce"
    stored = json.loads(lucebox._config_path().read_text(encoding="utf-8"))
    assert stored["instances"]["luce"]["model_path"] == "/m/target.gguf"
    # 既存設定の部分更新は他の値を保つ。
    lucebox.save_instance("luce", {"max_ctx": 32768})
    again = lucebox.get_instance("luce")
    assert again["max_ctx"] == 32768 and again["draft_path"] == "/m/draft.gguf"


def test_save_instance_rejects_out_of_range_values(lucebox):
    lucebox.save_instance("luce", {"model_path": "/m/t.gguf"})
    for patch in ({"max_ctx": 1}, {"draft_block_size": 64}, {"cache_type_k": "q2_0"},
                  {"port": 80}, {"draft_residency": "always"}):
        with pytest.raises(lucebox.LuceboxError):
            lucebox.save_instance("luce", patch)


def test_ports_do_not_collide_within_lucebox_or_with_llama(lucebox, monkeypatch):
    from app.models_mgmt import llama

    lucebox.save_instance("a", {"model_path": "/m/a.gguf", "port": 8216})
    with pytest.raises(lucebox.LuceboxError) as first:
        lucebox.save_instance("b", {"model_path": "/m/b.gguf", "port": 8216})
    assert "8216" in str(first.value)

    monkeypatch.setattr(llama, "endpoint_ports", lambda: {8080})
    with pytest.raises(lucebox.LuceboxError) as second:
        lucebox.save_instance("c", {"model_path": "/m/c.gguf", "port": 8080})
    assert "llama.cpp" in str(second.value)


def test_llama_rejects_a_port_taken_by_lucebox(monkeypatch, tmp_path):
    from app.models_mgmt import llama, lucebox as module

    monkeypatch.setattr(module, "endpoint_ports", lambda: {8216})
    with pytest.raises(ValueError) as excinfo:
        llama._ensure_port_free_for_other_runtimes(8216)
    assert "Lucebox" in str(excinfo.value)


def test_unit_content_uses_recommended_flags(lucebox, monkeypatch, tmp_path):
    monkeypatch.setattr(lucebox, "server_path", lambda: tmp_path / "dflash_server")
    lucebox.save_instance("luce", {"model_path": "/m/target.gguf", "draft_path": "/m/draft.gguf"})
    unit = lucebox._unit_content("luce")
    # systemd unit の引数は1つずつ quote される（systemd.py の _escape_exec_arg）。
    exec_start = next(line for line in unit.splitlines() if line.startswith("ExecStart="))
    assert '"--draft" "/m/draft.gguf"' in exec_start
    assert '"--draft-block-size" "16"' in exec_start
    assert '"--max-ctx" "131072"' in exec_start
    assert '"--cache-type-k" "q8_0"' in exec_start and '"--cache-type-v" "q8_0"' in exec_start
    assert '"--ddtree" "--ddtree-budget" "22"' in exec_start
    assert '"--fa-window" "0"' in exec_start
    # モデル名はControlDeck側のaliasと一致させる（ゲートウェイの解決に使う）。
    assert '"--model-name" "luce"' in exec_start
    # ROCm既知バグ対策はllama.cpp側と同じく全unitへ入れる。
    assert 'Environment="GPU_MAX_HW_QUEUES=1"' in unit


def test_unit_content_without_draft_has_no_speculative_flags(lucebox, monkeypatch, tmp_path):
    monkeypatch.setattr(lucebox, "server_path", lambda: tmp_path / "dflash_server")
    lucebox.save_instance("ar", {"model_path": "/m/target.gguf"})
    exec_start = next(line for line in lucebox._unit_content("ar").splitlines()
                      if line.startswith("ExecStart="))
    assert '"--draft"' not in exec_start and '"--ddtree"' not in exec_start


def test_delete_instance_keeps_shared_files(lucebox, monkeypatch, tmp_path):
    from app.applications import systemd as sd

    monkeypatch.setattr(sd, "stop", lambda _name: (True, ""))
    monkeypatch.setattr(sd, "set_enabled", lambda _name, _enabled: None)
    monkeypatch.setattr(sd, "remove_unit", lambda _name: None)
    target = tmp_path / "shared.gguf"
    target.write_bytes(b"x")
    lucebox.save_instance("a", {"model_path": str(target), "port": 8216})
    lucebox.save_instance("b", {"model_path": str(target), "port": 8217})
    result = lucebox.delete_instance("a", delete_file=True)
    assert result["gguf_deleted"] is False and target.exists()
    assert "他の設定" in result["reason"]


def test_track_warning_reflects_host_rocm(lucebox, monkeypatch):
    monkeypatch.setattr(lucebox, "host_rocm_version", lambda: "7.2.1")
    assert "ROCm 10" in lucebox.track_warning("rocm10")
    assert lucebox.track_warning("rocm7") == ""
    monkeypatch.setattr(lucebox, "host_rocm_version", lambda: "10.0.0")
    assert lucebox.track_warning("rocm10") == ""
    # 検出できないときも黙って進めない。
    monkeypatch.setattr(lucebox, "host_rocm_version", lambda: "")
    assert lucebox.track_warning("rocm10")


def test_recommended_track_falls_back_to_rocm10(lucebox, monkeypatch):
    monkeypatch.setattr(lucebox, "host_rocm_version", lambda: "7.2.1")
    assert lucebox.recommended_track() == "rocm7"
    monkeypatch.setattr(lucebox, "host_rocm_version", lambda: "")
    assert lucebox.recommended_track() == "rocm10"


def test_detect_requires_gfx1201_and_kfd(lucebox, monkeypatch):
    monkeypatch.setattr(lucebox, "_gfx_targets", lambda: [120001])
    monkeypatch.setattr(lucebox.os.path, "exists", lambda path: path == "/dev/kfd")
    assert lucebox.detect()["available"] is True
    monkeypatch.setattr(lucebox, "_gfx_targets", lambda: [110000])
    state = lucebox.detect()
    assert state["available"] is False and "gfx1201" in state["reason"]


def test_activate_points_current_at_the_package_root(lucebox, tmp_path):
    extracted = lucebox._version_root("lucebox-x-r1", "rocm10") / "extracted"
    binary = extracted / "lucebox-r9700" / "server" / "build" / "dflash_server"
    binary.parent.mkdir(parents=True)
    binary.write_bytes(b"ELF")
    result = lucebox._activate("lucebox-x-r1", "rocm10")
    assert lucebox.current_link().resolve().name == "lucebox-r9700"
    assert lucebox.get_config()["binary_relpath"] == "server/build/dflash_server"
    assert result["track"] == "rocm10"
    assert lucebox.server_path().is_file()
    assert lucebox.installed_versions() == [
        {"tag": "lucebox-x-r1", "track": "rocm10", "label": "ROCm 10", "current": True},
    ]


def test_health_reports_not_ok_without_a_server(lucebox):
    lucebox.save_instance("luce", {"model_path": "/m/t.gguf", "port": 8299})
    assert asyncio.run(lucebox.health("luce"))["ok"] is False
    assert asyncio.run(lucebox.health("missing"))["ok"] is False


def test_prefer_speculative_defaults_on(lucebox):
    """既定でONにする。OFFだと呼び出し側の既定temperature(0.4)で投機経路が使われない。"""
    saved = lucebox.save_instance("luce", {"model_path": "/m/t.gguf"})
    assert saved["prefer_speculative"] is True
    assert lucebox.save_instance("luce", {"prefer_speculative": False})["prefer_speculative"] is False


def test_instance_config_for_port_does_not_touch_systemd(lucebox, monkeypatch):
    """生成のたびに通る経路なので、systemctl を呼ぶ list_instances() を使わない。"""
    lucebox.save_instance("luce", {"model_path": "/m/t.gguf", "port": 8216})

    def _fail():
        raise AssertionError("list_instances() を呼んではいけない")

    monkeypatch.setattr(lucebox, "list_instances", _fail)
    found = lucebox.instance_config_for_port(8216)
    assert found is not None and found["alias"] == "luce"
    assert lucebox.instance_config_for_port(9999) is None


def test_lucebox_provider_pins_temperature_to_zero(lucebox, monkeypatch):
    """DFlash2 の検証は厳密グリーディのみ。temperature>0 だと自己回帰へ落ちる。"""
    from app.models_mgmt import runtime_provider as rp

    lucebox.save_instance("luce", {"model_path": "/m/t.gguf", "port": 8216})
    request = rp.RuntimeChatRequest(base_url="http://127.0.0.1:8216/v1", model="luce",
                                    messages=[{"role": "user", "content": "x"}])
    assert request.temperature > 0  # 呼び出し側の既定はサンプリング有効
    provider = rp.LuceboxRuntimeProvider()
    assert provider._payload(request, stream=True)["temperature"] == 0.0

    # OFF にしたら呼び出し側の値をそのまま通す。
    lucebox.save_instance("luce", {"prefer_speculative": False})
    assert provider._payload(request, stream=True)["temperature"] == request.temperature

    # 管理外のendpointへは触らない。
    external = rp.RuntimeChatRequest(base_url="http://example.com/v1", model="x",
                                     messages=[{"role": "user", "content": "x"}])
    assert provider._payload(external, stream=False)["temperature"] == external.temperature


def test_fa_window_is_not_passed_when_zero_full_attention(lucebox, monkeypatch, tmp_path):
    monkeypatch.setattr(lucebox, "server_path", lambda: tmp_path / "dflash_server")
    lucebox.save_instance("luce", {"model_path": "/m/t.gguf"})
    exec_start = next(line for line in lucebox._unit_content("luce").splitlines()
                      if line.startswith("ExecStart="))
    # 0 は「全注意」を意味する既定値なので、明示的に渡しても等価。値は必ず一致させる。
    assert '"--fa-window" "0"' in exec_start


def test_old_default_fa_window_is_migrated_to_zero(lucebox, monkeypatch, tmp_path):
    """fa_window=2048 は ControlDeck が誤って書いた既定値。ツール利用を壊すので直す。"""
    import json

    path = lucebox._config_path()
    path.write_text(json.dumps({
        "instances": {
            "a": {"model_path": "/m/a.gguf", "port": 8216, "fa_window": 2048},
            "b": {"model_path": "/m/b.gguf", "port": 8217, "fa_window": 1024},
        },
        "selected_alias": "a",
    }), encoding="utf-8")
    cfg = lucebox.get_config()
    assert cfg["instances"]["a"]["fa_window"] == 0
    # 利用者が意図して選んだ他の値は残す。
    assert cfg["instances"]["b"]["fa_window"] == 1024
    assert cfg["revision"] == lucebox.CONFIG_REVISION
    # 移行は書き戻して確定する（読むたびに走らせない）。
    assert json.loads(path.read_text(encoding="utf-8"))["instances"]["a"]["fa_window"] == 0
    # 移行後に意図して 2048 を選び直したら、次回以降は尊重する。
    lucebox.save_instance("a", {"fa_window": 2048})
    assert lucebox.get_config()["instances"]["a"]["fa_window"] == 2048


def test_runtime_status_warns_when_fa_window_breaks_tools(lucebox, monkeypatch):
    lucebox.save_instance("luce", {"model_path": "/m/t.gguf"})
    assert lucebox.runtime_status()["tool_warnings"] == {}
    lucebox.save_instance("luce", {"fa_window": 2048})
    warnings = lucebox.runtime_status()["tool_warnings"]
    assert "luce" in warnings and "ツール" in warnings["luce"]


def test_agent_turn_cache_defaults_on_and_is_passed_through(lucebox, monkeypatch, tmp_path):
    """ツールを重ねるエージェント用途で、ターンごとの再prefillを減らす。"""
    monkeypatch.setattr(lucebox, "server_path", lambda: tmp_path / "dflash_server")
    saved = lucebox.save_instance("luce", {"model_path": "/m/t.gguf"})
    assert saved["agent_turn_cache"] is True
    exec_start = next(line for line in lucebox._unit_content("luce").splitlines()
                      if line.startswith("ExecStart="))
    assert '"--agent-turn-cache"' in exec_start

    # 切ればフラグを渡さない（値を取らないフラグなので付ける/付けないで表す）。
    lucebox.save_instance("luce", {"agent_turn_cache": False})
    exec_start = next(line for line in lucebox._unit_content("luce").splitlines()
                      if line.startswith("ExecStart="))
    assert '"--agent-turn-cache"' not in exec_start
