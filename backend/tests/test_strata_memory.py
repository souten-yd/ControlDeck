"""Memory planning must distinguish disk, pinned host KV and device allocations."""
from app.models_mgmt import strata_memory as memory


def config(**changes):
    return {"family": "unsloth", "model": "ud-iq4_xs", "context": "256k", "kv": "int8",
            "parallel": 6, "kv_resident": 32768, **changes}


def machine():
    return {"ram_total": 91 * memory.GIB, "ram_available": 87 * memory.GIB, "gpu": None}


def test_context_is_per_conversation_and_quantization_changes_capacity():
    info = {"expert_bytes": 55 * memory.GIB, "disk_bytes": 200 * memory.GIB}
    result = memory.estimate(config(), machine(), info)
    assert result["sessions"] == 7
    assert result["context_tokens"] == 262144
    assert 23 < result["kv_ram_bytes"] / memory.GIB < 24
    # Disk size is not added to resident RAM: PLE weights may stay file-mapped.
    assert result["full_ram_estimate_bytes"] < 90 * memory.GIB
    q4 = memory.estimate(config(kv="q4_0"), machine(), info)
    assert q4["kv_ram_bytes"] < result["kv_ram_bytes"]
    assert q4["full_ram_context_tokens"] > result["full_ram_context_tokens"]
    one = memory.estimate(config(parallel=1), machine(), info)
    assert one["sessions"] == 1
    assert one["kv_ram_bytes"] * 7 == result["kv_ram_bytes"]


def test_unknown_model_or_auto_window_does_not_invent_capacity():
    result = memory.estimate(config(kv_resident=None), machine(), {"expert_bytes": None})
    assert result["full_ram_context_tokens"] is None
    assert result["kv_vram_min_bytes"] < result["kv_vram_max_bytes"]
    assert not memory.estimate(config(family="deepseek41"), machine(), {})["supported"]


def test_zero_ram_reserve_is_not_default_four():
    result = memory.estimate(config(ram_keep_gib=0), machine(), {"expert_bytes": 55 * memory.GIB})
    assert result["ram_keep_bytes"] == 0


def test_log_categories_and_restart_do_not_double_count():
    text = '''engine started: old
strata generate: loaded 99.00 GiB at 1 GiB/s
engine started: new
strata generate: KV streaming: 32768 of 262144 cells per QSA layer in VRAM, the K/V in 3.09 GiB of pinned RAM
strata generate: --batch: 6 slot sessions on CUDA0 (0.95 GiB each); 19 GiB free
strata generate: loaded 55.43 GiB at 2 GiB/s
strata generate: expert cache 7401 slots, 16.68 GiB of VRAM
strata mtp: draft layer loaded, 835 MiB of VRAM
'''
    rows = memory.allocations(text, 6)
    host_model = next(row for row in rows if row["category"] == "モデル本体・エキスパート")
    assert abs(host_model["bytes"] / memory.GIB - 55.43) < .001
    assert any(row["category"].startswith("VLM") and row["bytes"] is None for row in rows)
    assert abs(next(row for row in rows if row["location"] == "VRAM" and row["category"].startswith("KV"))["bytes"] / memory.GIB - 6.65) < .001


def test_ssd_fallback_reports_ram_complement():
    rows = memory.allocations("FileExpertSource: allocating 21.88 GiB page-locked cache complement", 6)
    assert abs(next(row for row in rows if row["category"] == "モデル本体・エキスパート")["bytes"] / memory.GIB - 21.88) < .001


def test_estimate_endpoints_validate_and_do_not_start_models(admin_client, monkeypatch):
    from app.models_mgmt import strata_gateway
    monkeypatch.setattr(strata_gateway, "_run", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("must not launch")))
    monkeypatch.setattr(memory, "model_info", lambda cfg: {"disk_bytes": None, "expert_bytes": None})
    body = {"alias": "memory", "family": "unsloth", "model": "ud-iq4_xs", "context": "256k", "parallel": 6, "kv_resident": 32768}
    response = admin_client.post("/api/v1/models/strata/memory-estimate", json=body, headers={"X-Requested-With": "ControlDeck"})
    assert response.status_code == 200
    assert response.json()["sessions"] == 7
    body["context"] = "2048k"
    assert admin_client.post("/api/v1/models/strata/memory-estimate", json=body, headers={"X-Requested-With": "ControlDeck"}).status_code == 422


def test_observed_stopped_does_not_read_stale_logs(monkeypatch):
    from app.models_mgmt import strata_gateway
    monkeypatch.setattr(strata_gateway, "get_instance", lambda alias: {"runtime_status": "STOPPED"})
    monkeypatch.setattr(memory, "process_usage", lambda pid: (_ for _ in ()).throw(AssertionError("no processes while stopped")))
    result = memory.observed("stopped", {"parallel": 6, "port": 8096})
    assert result["rows"] == []
    assert result["processes"] == []


def test_explicit_full_device_kv_does_not_allocate_host_streaming_backing():
    info = {"expert_bytes": 55 * memory.GIB}
    result = memory.estimate(config(context="32k", kv_resident=32768), machine(), info)
    assert result["kv_ram_bytes"] == 0
    assert result["full_ram_estimate_bytes"] == 59 * memory.GIB


def test_runtime_status_shape_without_child_pid_uses_owned_host_record(monkeypatch, tmp_path):
    from types import SimpleNamespace
    from app.models_mgmt import strata, strata_gateway
    import json
    import os
    monkeypatch.setattr(strata, "_home", lambda: tmp_path)
    root = tmp_path / ".local/state/strata/runtime"
    root.mkdir(parents=True)
    (root / "launch-cd-memory.json").write_text(json.dumps({"profile": "launch-cd-memory", "host_pid": 1234}))
    monkeypatch.setattr(memory.psutil, "Process", lambda pid: SimpleNamespace(uids=lambda: SimpleNamespace(real=os.getuid()), cmdline=lambda: ["python", "-m", "strata_control.runtime", "--host", "launch-cd-memory"], create_time=lambda: 0))
    # Terminal's launch --status rows do not expose child_pid.
    monkeypatch.setattr(strata_gateway, "get_instance", lambda alias: {"runtime_status": "READY"})
    seen = []
    monkeypatch.setattr(memory, "process_usage", lambda pid: seen.append(pid) or [{"category": "VLM", "ram_bytes": 123, "vram_bytes": None}])
    result = memory.observed("memory", {"parallel": 6, "port": 8096})
    assert seen == [1234]
    assert result["processes"][0]["ram_bytes"] == 123
