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
                             "instance": {"port": 9001, "n_gpu_layers": 32, "bogus": 1}})
    assert cfg["tag"] == "t1" and cfg["backend"] == "rocm"
    assert cfg["instance"]["port"] == 9001 and cfg["instance"]["n_gpu_layers"] == 32
    assert "bogus" not in cfg["instance"]  # 未知キーは無視
    # 再読込
    assert llama.get_config()["instance"]["port"] == 9001


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
    assert "LD_LIBRARY_PATH=" in content  # 共有ライブラリパス


def test_llama_api_status(admin_client):
    r = admin_client.get("/api/v1/models/llama/status")
    assert r.status_code == 200
    assert "installed" in r.json() and r.json()["experimental"] is True
