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
    # ポートは共有できる（同一エンドポイントに束ね、起動時に排他制御する）。
    shared = llama.save_instance(
        "model-c", {"alias": "model-c", "model_path": "/models/c.gguf", "port": 8080},
    )
    assert shared["instances"]["model-c"]["endpoint_id"] == shared["instances"]["model-a"]["endpoint_id"]
    assert {i["alias"] for i in llama.instances_on_endpoint(
        shared["instances"]["model-a"]["endpoint_id"])} == {"model-a", "model-c"}
    # 新規登録は既定チャット先を引き継ぐ（従来どおり）
    assert shared["selected_alias"] == "model-c"
    # 既存モデルの保存では既定チャット先を奪わない（利用中のモデルが黙って変わらない）
    resaved = llama.save_instance("model-a", {"ctx_size": 16384})
    assert resaved["selected_alias"] == "model-c"
    assert resaved["instances"]["model-a"]["ctx_size"] == 16384
    # 同じGGUFを別設定で持てる（別CTXで切り替える複製の用途）
    same_file = llama.save_instance(
        "model-d", {"alias": "model-d", "model_path": "/models/a.gguf", "port": 8080},
    )
    assert same_file["instances"]["model-d"]["model_path"] == "/models/a.gguf"


def test_resolve_instance_by_port_prefers_running_then_active_then_priority(monkeypatch, tmp_path):
    """同一ポートを共有したとき、どのモデルがそのポートを代表するかを1件に決める。"""
    from app.models_mgmt import llama

    monkeypatch.setattr(llama, "_config_path", lambda: tmp_path / "resolve.json")
    monkeypatch.setattr(llama, "is_installed", lambda: False)
    llama.save_instance("first", {"alias": "first", "model_path": "/models/a.gguf", "port": 9100})
    llama.save_instance("second", {"alias": "second", "model_path": "/models/b.gguf", "port": 9100})
    llama.reorder_instances(["second", "first"])

    # 稼働中が無ければ最優先（=並びの先頭）
    assert llama.resolve_instance_by_port(9100) == "second"

    # 最後に起動したものが記録されていればそれを優先
    llama._set_active_alias(llama.get_config()["instances"]["first"]["endpoint_id"], "first")
    assert llama.resolve_instance_by_port(9100) == "first"

    # 稼働中があれば最優先
    from app.applications import systemd as sd

    monkeypatch.setattr(sd, "query_status", lambda name: (
        {"status": "RUNNING"} if name == llama.unit_name("second") else {"status": "STOPPED"}
    ))
    assert llama.resolve_instance_by_port(9100) == "second"
    assert 9100 in llama.endpoint_ports()
    assert llama.resolve_instance_by_port(9999) is None


def test_reorder_sets_priority_and_autostart_picks_top_of_endpoint(monkeypatch, tmp_path):
    """同一エンドポイントで auto_start が複数あっても、enable するのは最優先の1件だけ。"""
    from app.applications import systemd as sd
    from app.models_mgmt import llama

    monkeypatch.setattr(llama, "_config_path", lambda: tmp_path / "order.json")
    monkeypatch.setattr(llama, "is_installed", lambda: True)
    monkeypatch.setattr(llama, "_unit_content", lambda alias=None: "unit")
    monkeypatch.setattr(sd, "query_status", lambda name: {"status": "STOPPED"})
    monkeypatch.setattr(sd, "write_unit", lambda name, content: None)
    enabled: dict[str, bool] = {}
    monkeypatch.setattr(sd, "set_enabled", lambda name, value: enabled.__setitem__(name, value))
    for alias in ("a", "b"):
        path = tmp_path / f"{alias}.gguf"
        path.write_bytes(b"GGUF")
        llama.save_instance(alias, {"alias": alias, "model_path": str(path),
                                    "port": 9200, "auto_start": True})
    assert enabled[llama.unit_name("a")] is True
    assert enabled[llama.unit_name("b")] is False

    llama.reorder_instances(["b", "a"])
    assert [i["alias"] for i in llama.list_instances()] == ["b", "a"]
    assert enabled[llama.unit_name("b")] is True
    assert enabled[llama.unit_name("a")] is False


def test_duplicate_instance_shares_endpoint_without_stealing_autostart(monkeypatch, tmp_path):
    from app.models_mgmt import llama

    monkeypatch.setattr(llama, "_config_path", lambda: tmp_path / "dup.json")
    monkeypatch.setattr(llama, "is_installed", lambda: False)
    llama.save_instance("base", {"alias": "base", "model_path": "/models/a.gguf",
                                 "port": 9300, "auto_start": True, "ctx_size": 8192})
    cfg = llama.duplicate_instance("base", "base-long-ctx")
    copy = cfg["instances"]["base-long-ctx"]
    assert copy["endpoint_id"] == cfg["instances"]["base"]["endpoint_id"]
    assert copy["ctx_size"] == 8192
    assert copy["auto_start"] is False


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
    # 同じポートの共有は許可される（エンドポイントを共有し、起動時に排他制御する）
    shared = admin_client.post("/api/v1/models/llama/instances", json={
        "alias": "catalog-c", "model_path": str(gguf_c), "port": 8202,
    }, headers=CSRF_HEADERS)
    assert shared.status_code == 201, shared.text
    endpoints = admin_client.get("/api/v1/models/llama/endpoints")
    assert endpoints.status_code == 200
    shared_endpoint = next(e for e in endpoints.json() if e["port"] == 8202)
    assert set(shared_endpoint["aliases"]) == {"catalog-b", "catalog-c"}
    # 優先度の並べ替え
    reordered = admin_client.post("/api/v1/models/llama/instances/reorder", json={
        "order": ["catalog-c", "catalog-a", "catalog-b"],
    }, headers=CSRF_HEADERS)
    assert reordered.status_code == 200
    assert [i["alias"] for i in reordered.json()] == ["catalog-c", "catalog-a", "catalog-b"]
    # 所属モデルがあるエンドポイントは削除できない
    busy = admin_client.post(
        f"/api/v1/models/llama/endpoints/{shared_endpoint['id']}/delete", headers=CSRF_HEADERS,
    )
    assert busy.status_code == 409
    deleted = admin_client.post("/api/v1/models/llama/instances/catalog-b/delete", headers=CSRF_HEADERS)
    assert deleted.status_code == 200 and deleted.json()["gguf_deleted"] is False
    assert gguf_b.exists()


def test_llama_vision_detection_is_same_folder_and_disabled_by_default(admin_client, monkeypatch):
    from app.models_mgmt import llama
    from tests.conftest import CSRF_HEADERS, _sandbox

    model_dir = _sandbox / "vision-detection"
    model_dir.mkdir()
    monkeypatch.setattr(llama, "_config_path", lambda: model_dir / "llama-runtime.json")
    model = model_dir / "Qwen3.8-27B-Q4_K_M.gguf"
    model.write_bytes(b"GGUF-model")
    projector_b = model_dir / "mmproj-F16.gguf"
    projector_a = model_dir / "MMProj-BF16.GGUF"
    projector_b.write_bytes(b"GGUF-projector-b")
    projector_a.write_bytes(b"GGUF-projector-a")
    (model_dir / "not-a-projector.gguf").write_bytes(b"GGUF-other")
    outside = _sandbox / "mmproj-outside.gguf"
    outside.write_bytes(b"GGUF-outside")
    (model_dir / "mmproj-link.gguf").symlink_to(outside)

    response = admin_client.get(
        "/api/v1/models/llama/vision-detection",
        params={"model_path": str(model)},
        headers=CSRF_HEADERS,
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload == {
        "available": True,
        "candidates": [str(projector_a.resolve()), str(projector_b.resolve())],
        "suggested_path": str(projector_a.resolve()),
        "enabled_by_default": False,
    }

    created = admin_client.post(
        "/api/v1/models/llama/instances",
        json={"alias": "vision-default-off", "model_path": str(model), "port": 8203},
        headers=CSRF_HEADERS,
    )
    assert created.status_code == 201, created.text
    instance = next(item for item in created.json()["instances"].values()
                    if item["alias"] == "vision-default-off")
    assert instance["mmproj_path"] == ""


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


def test_start_instance_restarts_running_unit_to_apply_saved_settings(monkeypatch, tmp_path):
    """保存済み設定が確実に反映されること。

    save_instance が先に unit を書き出すため、start_instance 側で unit ファイルとの
    差分を見ても常に「変更なし」になり、稼働中は start（no-op）に落ちて設定が
    反映されないバグがあった。稼働中は必ず restart する。
    """
    from app.applications import systemd as sd
    from app.models_mgmt import llama

    monkeypatch.setattr(llama, "_config_path", lambda: tmp_path / "restart.json")
    monkeypatch.setattr(llama, "is_installed", lambda: True)
    monkeypatch.setattr(llama, "server_path", lambda: tmp_path / "llama-server")
    monkeypatch.setattr(llama, "_unit_content", lambda alias=None: "unit")
    monkeypatch.setattr(llama, "mark_used_by_base_url", lambda url: None)
    monkeypatch.setattr("app.models_mgmt.runtime_policy.ensure_gpu_profile",
                        lambda **kwargs: {})
    monkeypatch.setattr(llama.time, "sleep", lambda seconds: None)
    gguf = tmp_path / "m.gguf"
    gguf.write_bytes(b"GGUF")

    calls: list[str] = []
    monkeypatch.setattr(sd, "write_unit", lambda name, content: None)
    monkeypatch.setattr(sd, "reset_failed", lambda name: None)
    monkeypatch.setattr(sd, "set_enabled", lambda name, value: None)
    monkeypatch.setattr(sd, "stop", lambda name: (True, ""))
    monkeypatch.setattr(sd, "query_status", lambda name: {"status": "RUNNING"})
    monkeypatch.setattr(sd, "restart", lambda name: (calls.append("restart"), (True, ""))[1])
    monkeypatch.setattr(sd, "start", lambda name: (calls.append("start"), (True, ""))[1])

    llama.save_instance("m", {"alias": "m", "model_path": str(gguf), "port": 9600})
    ok, _ = llama.start_instance("m")
    assert ok is True
    assert calls == ["restart"], "稼働中は restart で設定を作り直す"

    calls.clear()
    monkeypatch.setattr(sd, "query_status", lambda name: {"status": "STOPPED"})
    llama.start_instance("m")
    assert calls == ["start"], "停止中は start"


def test_delete_instance_can_remove_gguf_but_protects_shared_files(monkeypatch, tmp_path):
    """GGUF本体の削除は取り消せないので、他が参照中なら消さない。"""
    from app.applications import systemd as sd
    from app.models_mgmt import llama
    from tests.conftest import _sandbox

    monkeypatch.setattr(llama, "_config_path", lambda: tmp_path / "del.json")
    monkeypatch.setattr(llama, "is_installed", lambda: False)
    monkeypatch.setattr(llama, "stop_instance", lambda alias=None: (True, ""))
    monkeypatch.setattr(sd, "remove_unit", lambda name: None)
    shared = _sandbox / "shared.gguf"
    shared.write_bytes(b"GGUF")
    lone = _sandbox / "lone.gguf"
    lone.write_bytes(b"GGUF")

    llama.save_instance("a", {"alias": "a", "model_path": str(shared), "port": 9700})
    llama.save_instance("b", {"alias": "b", "model_path": str(shared), "port": 9701})
    llama.save_instance("c", {"alias": "c", "model_path": str(lone), "port": 9702})

    # 他が参照中（b）なら本体は消さない
    result = llama.delete_instance("a", delete_file=True)
    assert result["gguf_deleted"] is False
    assert "b" in result["reason"]
    assert shared.exists()

    # 既定（delete_file なし）は設定だけ消す
    result = llama.delete_instance("b")
    assert result["gguf_deleted"] is False
    assert shared.exists()

    # 参照が無ければ消す
    result = llama.delete_instance("c", delete_file=True)
    assert result["gguf_deleted"] is True
    assert not lone.exists()


def test_delete_instance_keeps_selection_when_other_model_removed(monkeypatch, tmp_path):
    """使っていないモデルを消しただけで既定チャット先が変わらない。"""
    from app.applications import systemd as sd
    from app.models_mgmt import llama

    monkeypatch.setattr(llama, "_config_path", lambda: tmp_path / "sel.json")
    monkeypatch.setattr(llama, "is_installed", lambda: False)
    monkeypatch.setattr(llama, "stop_instance", lambda alias=None: (True, ""))
    monkeypatch.setattr(sd, "remove_unit", lambda name: None)
    llama.save_instance("keep", {"alias": "keep", "model_path": "/m/k.gguf", "port": 9710})
    llama.save_instance("drop", {"alias": "drop", "model_path": "/m/d.gguf", "port": 9711})
    llama.select_instance("keep")
    llama.delete_instance("drop")
    assert llama.get_config()["selected_alias"] == "keep"
