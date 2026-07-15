"""llama.cpp ランタイム管理のテスト（DL/GPU 不要のロジック部分）。"""


def test_backend_asset_matching():
    from app.models_mgmt import llama

    assets = [
        {"name": "llama-linux-amd-vulkan-b10001.tar.gz", "size": 1, "browser_download_url": "u"},
        {"name": "llama-linux-cuda-b10001.tar.gz", "size": 1, "browser_download_url": "u"},
        {"name": "llama-linux-rocm-r9700-b10001.tar.gz", "size": 1, "browser_download_url": "u"},
        {"name": "llama-windows-vulkan-x64.zip", "size": 1, "browser_download_url": "u"},
    ]
    # パターンで backend 判別
    matched = {}
    for a in assets:
        for b, pat in llama.BACKEND_PATTERNS.items():
            if pat.search(a["name"]):
                matched[b] = a["name"]
    assert matched["vulkan"].endswith("vulkan-b10001.tar.gz")
    assert matched["rocm"].endswith("rocm-r9700-b10001.tar.gz")
    assert matched["cuda"].endswith("cuda-b10001.tar.gz")
    assert "windows" not in str(matched)  # Windows zip は対象外


def test_config_roundtrip(client, monkeypatch, tmp_path):
    from app.models_mgmt import llama

    monkeypatch.setattr(llama, "_config_path", lambda: tmp_path / "llama-runtime.json")
    cfg = llama.save_config({"tag": "t1", "backend": "rocm",
                             "instance": {"port": 9001, "n_gpu_layers": 32,
                                          "cache_type_k": "q8_0", "spec_type": "draft-mtp",
                                          "extra_args": "--unsafe", "bogus": 1}})
    assert cfg["tag"] == "t1" and cfg["backend"] == "rocm"
    assert cfg["instance"]["port"] == 9001 and cfg["instance"]["n_gpu_layers"] == 32
    assert "bogus" not in cfg["instance"]  # 未知キーは無視
    assert "extra_args" not in cfg["instance"]
    assert cfg["instance"]["cache_type_k"] == "q8_0"
    # 再読込
    assert llama.get_config()["instance"]["port"] == 9001


def test_old_config_is_migrated_with_new_typed_defaults(monkeypatch, tmp_path):
    import json
    from app.models_mgmt import llama

    path = tmp_path / "llama-runtime.json"
    path.write_text(json.dumps({"backend": "vulkan", "instance": {
        "model_path": "/models/old.gguf", "ctx_size": 2048, "extra_args": "--unsafe",
    }}))
    monkeypatch.setattr(llama, "_config_path", lambda: path)
    instance = llama.get_config()["instance"]
    assert instance["model_path"] == "/models/old.gguf" and instance["ctx_size"] == 2048
    assert instance["n_predict"] == 2048 and instance["cache_type_k"] == "f16"
    assert "extra_args" not in instance


def test_status_shape():
    from app.models_mgmt import llama

    st = llama.runtime_status()
    assert set(st) >= {"installed", "backend", "base_url", "experimental"}
    assert st["experimental"] is True


def test_unit_content_requires_model(monkeypatch, tmp_path):
    from app.models_mgmt import llama

    monkeypatch.setattr(llama, "_config_path", lambda: tmp_path / "c.json")
    llama.save_config({"instance": {"model_path": ""}})
    import pytest

    with pytest.raises(RuntimeError):
        llama._unit_content()


def test_unit_content_generation(monkeypatch, tmp_path):
    from app.models_mgmt import llama

    monkeypatch.setattr(llama, "_config_path", lambda: tmp_path / "c.json")
    monkeypatch.setattr(llama, "current_link", lambda: tmp_path / "current")
    (tmp_path / "current").mkdir()
    llama.save_config({"instance": {"model_path": "/models/m.gguf", "port": 8080,
                                    "n_gpu_layers": 999, "flash_attn": True}})
    content = llama._unit_content()
    assert "--model" in content and "/models/m.gguf" in content
    assert "--port" in content and "8080" in content
    assert "--flash-attn" in content
    assert "--n-predict" in content and "2048" in content
    assert "--cache-type-k" in content and "f16" in content
    assert "--batch-size" in content and "--ubatch-size" in content
    assert "LD_LIBRARY_PATH=" in content  # 共有ライブラリパス


def test_unit_content_typed_mtp_moe_and_cache(monkeypatch, tmp_path):
    from app.models_mgmt import llama

    monkeypatch.setattr(llama, "_config_path", lambda: tmp_path / "c.json")
    monkeypatch.setattr(llama, "current_link", lambda: tmp_path / "current")
    (tmp_path / "current").mkdir()
    llama.save_config({"instance": {
        "model_path": "/models/mtp.gguf", "cache_type_k": "q8_0", "cache_type_v": "q4_0",
        "spec_type": "draft-mtp", "draft_max": 8, "cpu_moe": True,
        "mmap": False, "mlock": True,
    }})
    content = llama._unit_content()
    assert "--cache-type-k" in content and "q8_0" in content
    assert "--cache-type-v" in content and "q4_0" in content
    assert "--spec-type" in content and "draft-mtp" in content and "--draft-max" in content
    assert "--cpu-moe" in content and "--no-mmap" in content and "--mlock" in content


def test_llama_api_status(admin_client):
    r = admin_client.get("/api/v1/models/llama/status")
    assert r.status_code == 200
    assert "installed" in r.json() and r.json()["experimental"] is True


def test_llama_config_api_rejects_untyped_args_and_bad_values(admin_client):
    headers = {"X-Requested-With": "ControlDeck"}
    response = admin_client.put(
        "/api/v1/models/llama/instance", json={"extra_args": "--host 0.0.0.0"}, headers=headers,
    )
    assert response.status_code == 422
    response = admin_client.put(
        "/api/v1/models/llama/instance", json={"cache_type_k": "q2_unsafe"}, headers=headers,
    )
    assert response.status_code == 422
    response = admin_client.put(
        "/api/v1/models/llama/instance", json={"model_path": "../../etc/passwd"}, headers=headers,
    )
    assert response.status_code == 422
