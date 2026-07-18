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
    cfg = llama.get_config()
    assert cfg["selected_alias"] == "llama" and cfg["instances"]["llama"]["model_path"] == "/models/old.gguf"


def test_multi_instance_catalog_uniqueness_and_unit_names(monkeypatch, tmp_path):
    import pytest
    from app.models_mgmt import llama

    monkeypatch.setattr(llama, "_config_path", lambda: tmp_path / "multi.json")
    first = llama.save_instance("model-a", {"alias": "model-a", "model_path": "/models/a.gguf", "port": 8080})
    second = llama.save_instance("model-b", {"alias": "model-b", "model_path": "/models/b.gguf", "port": 8081})
    assert set(second["instances"]) == {"model-a", "model-b"}
    assert second["selected_alias"] == "model-b"
    assert first["instances"]["model-a"]["auto_start"] is False
    assert llama.unit_name("model-a") != llama.unit_name("model-b")
    assert llama.unit_name("model-a").startswith("cdapp-llama-model-a-")
    with pytest.raises(ValueError, match="port 8080"):
        llama.save_instance("model-c", {"alias": "model-c", "model_path": "/models/c.gguf", "port": 8080})
    with pytest.raises(ValueError, match="同じGGUF"):
        llama.save_instance("model-c", {"alias": "model-c", "model_path": "/models/a.gguf", "port": 8082})


def test_mark_used_matches_local_instance_port(monkeypatch, tmp_path):
    from app.models_mgmt import llama

    monkeypatch.setattr(llama, "_config_path", lambda: tmp_path / "usage.json")
    llama.save_instance("used", {"alias": "used", "model_path": "/models/u.gguf", "port": 8123})
    assert llama.mark_used_by_base_url("http://127.0.0.1:8123/v1") == "used"
    assert llama.get_instance("used")["last_used_at"]
    assert llama.mark_used_by_base_url("https://remote.example/v1") is None


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
    llama.save_config({"backend": "rocm", "instance": {
        "model_path": "/models/mtp.gguf", "cache_type_k": "q8_0", "cache_type_v": "q4_0",
        "spec_type": "draft-mtp", "draft_max": 8, "cpu_moe": True,
        "mmap": False, "mlock": True,
    }})
    content = llama._unit_content()
    assert "--cache-type-k" in content and "q8_0" in content
    assert "--cache-type-v" in content and "q4_0" in content
    assert "--spec-type" in content and "draft-mtp" in content and "--spec-draft-n-max" in content
    assert "--draft-max" not in content  # b10001で削除された旧引数を出さない
    assert '"--flash-attn" "off"' in content  # 値必須形式（裸フラグは起動エラーになる）
    assert "--cpu-moe" in content and "--no-mmap" in content and "--mlock" in content
    # ROCmはHIPストリーム複数時のアイドル100%バグ回避（ROCm/ROCm#2625）
    assert 'Environment="GPU_MAX_HW_QUEUES=1"' in content


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


def test_llama_multi_instance_api(admin_client, monkeypatch):
    from app.applications import systemd as sd
    from app.models_mgmt import llama
    from tests.conftest import CSRF_HEADERS, _sandbox

    config_path = _sandbox / "llama-multi-test.json"
    gguf_a = _sandbox / "catalog-a.gguf"
    gguf_b = _sandbox / "catalog-b.gguf"
    gguf_a.write_bytes(b"GGUF-a")
    gguf_b.write_bytes(b"GGUF-b")
    monkeypatch.setattr(llama, "_config_path", lambda: config_path)
    monkeypatch.setattr(llama, "is_installed", lambda: False)
    monkeypatch.setattr(llama, "stop_instance", lambda alias=None: (True, ""))
    monkeypatch.setattr(sd, "remove_unit", lambda name: None)
    monkeypatch.setattr(sd, "query_status", lambda name: {"status": "STOPPED"})

    first = admin_client.post("/api/v1/models/llama/instances", json={
        "alias": "catalog-a", "model_path": str(gguf_a), "port": 8201,
    }, headers=CSRF_HEADERS)
    assert first.status_code == 201, first.text
    second = admin_client.post("/api/v1/models/llama/instances", json={
        "alias": "catalog-b", "model_path": str(gguf_b), "port": 8202,
        "cache_type_k": "q8_0", "idle_exclude": True,
    }, headers=CSRF_HEADERS)
    assert second.status_code == 201, second.text
    listed = admin_client.get("/api/v1/models/llama/instances")
    assert listed.status_code == 200
    assert {item["alias"] for item in listed.json()} == {"catalog-a", "catalog-b"}
    gguf_c = _sandbox / "catalog-c.gguf"
    gguf_c.write_bytes(b"GGUF-c")
    duplicate = admin_client.post("/api/v1/models/llama/instances", json={
        "alias": "catalog-c", "model_path": str(gguf_c), "port": 8202,
    }, headers=CSRF_HEADERS)
    assert duplicate.status_code == 422 and "port 8202" in duplicate.text
    deleted = admin_client.post("/api/v1/models/llama/instances/catalog-b/delete", headers=CSRF_HEADERS)
    assert deleted.status_code == 200 and deleted.json()["gguf_deleted"] is False
    assert gguf_b.exists()


def test_unit_content_role_embedding_and_reranker(monkeypatch, tmp_path):
    from app.models_mgmt import llama

    monkeypatch.setattr(llama, "_config_path", lambda: tmp_path / "c.json")
    monkeypatch.setattr(llama, "current_link", lambda: tmp_path / "current")
    monkeypatch.setattr(llama, "sync_instance_unit", lambda alias: None)
    (tmp_path / "current").mkdir()
    llama.save_instance("embed", {"alias": "embed", "model_path": "/models/bge-m3.gguf",
                                  "role": "embedding", "port": 8091, "spec_type": "draft-mtp"})
    llama.save_instance("rerank", {"alias": "rerank", "model_path": "/models/qwen3-reranker.gguf",
                                   "role": "reranker", "port": 8092})
    embed_unit = llama._unit_content("embed")
    assert "--embedding" in embed_unit and "--pooling" in embed_unit
    # embedding/reranker では投機的デコーディングを付けない
    assert "--spec-type" not in embed_unit
    rerank_unit = llama._unit_content("rerank")
    assert "--rerank" in rerank_unit and "--embedding" not in rerank_unit


def test_find_role_instance(monkeypatch, tmp_path):
    from app.models_mgmt import llama

    monkeypatch.setattr(llama, "_config_path", lambda: tmp_path / "c.json")
    monkeypatch.setattr(llama, "sync_instance_unit", lambda alias: None)
    llama.save_instance("chatm", {"alias": "chatm", "model_path": "/models/chat.gguf", "port": 8090})
    llama.save_instance("embed", {"alias": "embed", "model_path": "/models/bge.gguf",
                                  "role": "embedding", "port": 8091})
    found = llama.find_role_instance("embedding")
    assert found is not None and found["alias"] == "embed"
    assert llama.find_role_instance("reranker") is None
    assert llama.find_role_instance("llm")["alias"] == "chatm"
