"""ローカル GGUF 登録（スキャン・名前提案・API）のテスト。"""
import pytest

from tests.conftest import CSRF_HEADERS, _sandbox


def _mk(path, size=16):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"G" * size)


def test_scan_gguf_finds_files_with_depth_limit():
    from app.models_mgmt.ollama import scan_gguf

    base = _sandbox / "gguf-scan"
    _mk(base / "a.gguf", 8)
    _mk(base / "sub" / "b.GGUF", 8)
    _mk(base / "sub" / "note.txt", 4)
    _mk(base / "d1" / "d2" / "d3" / "d4" / "deep.gguf", 8)  # 深さ 4 → 除外

    found = scan_gguf(str(base))
    names = {f["name"] for f in found}
    assert names == {"a.gguf", "b.GGUF"}
    assert all(f["size"] == 8 for f in found)


def test_scan_gguf_rejects_outside_roots_and_files():
    from app.files.service import FileAccessError
    from app.models_mgmt.ollama import OllamaError, scan_gguf

    with pytest.raises(FileAccessError):
        scan_gguf("/etc")
    target = _sandbox / "gguf-scan" / "a.gguf"
    with pytest.raises(OllamaError):
        scan_gguf(str(target))  # ファイル指定はエラー


def test_suggest_model_name():
    from app.models_mgmt.ollama import suggest_model_name

    assert suggest_model_name("Qwen2.5-7B-Instruct-Q4_K_M.gguf") == "qwen2.5-7b-instruct-q4_k_m"
    assert suggest_model_name("日本語モデル.gguf") == "local-model"


def test_list_models_matches_running_alias_and_digest(monkeypatch):
    """tagsとpsの表記差があってもロード状態を失わない。"""
    import asyncio

    from app.models_mgmt import ollama

    class TagsResponse:
        status_code = 200

        @staticmethod
        def json():
            return {"models": [
                {"name": "Qwen:latest", "digest": "ABC", "details": {}},
                {"name": "digest-only:latest", "digest": "DEF", "details": {}},
            ]}

    async def tags(_path):
        return TagsResponse()

    async def running():
        return [
            {"model": "qwen", "size_vram": 10},
            {"name": "renamed:latest", "digest": "def", "size_vram": 20},
        ]

    monkeypatch.setattr(ollama, "_get", tags)
    monkeypatch.setattr(ollama, "running_models", running)
    models = asyncio.run(ollama.list_models())
    assert [(item["loaded"], item["vram"]) for item in models] == [(True, 10), (True, 20)]
    assert ollama.normalize_model_name("hf.co/Org/Model") == "hf.co/org/model:latest"


def test_gguf_scan_endpoint(admin_client):
    base = _sandbox / "gguf-scan"
    _mk(base / "a.gguf", 8)
    r = admin_client.get(f"/api/v1/models/gguf-scan?path={base}")
    assert r.status_code == 200, r.text
    files = r.json()["files"]
    assert any(f["name"] == "a.gguf" and f["suggest_name"] == "a" for f in files)

    r = admin_client.get("/api/v1/models/gguf-scan?path=/etc")
    assert r.status_code == 403


def test_register_rejects_bad_name_and_non_gguf():
    import asyncio

    from app.models_mgmt.ollama import OllamaError, register_gguf_stream

    _mk(_sandbox / "gguf-scan" / "a.gguf", 8)
    _mk(_sandbox / "gguf-scan" / "note.txt", 4)

    async def consume(name, path):
        async for _ in register_gguf_stream(name, path):
            pass

    with pytest.raises(OllamaError):
        asyncio.run(consume("bad name!", str(_sandbox / "gguf-scan" / "a.gguf")))
    with pytest.raises(OllamaError):
        asyncio.run(consume("ok-name", str(_sandbox / "gguf-scan" / "note.txt")))


def test_model_config_crud(admin_client):
    """モデル個別設定（keep_alive/idle_exclude）の保存・取得・クリア。"""
    from app.models_mgmt import ollama

    r = admin_client.put("/api/v1/models/qwen2.5%3A7b/config",
                         json={"keep_alive": "1h", "idle_exclude": True}, headers=CSRF_HEADERS)
    assert r.status_code == 200, r.text
    assert ollama.effective_keep_alive("qwen2.5:7b") == "1h"
    assert ollama.get_model_config("qwen2.5:7b")["idle_exclude"] is True
    got = admin_client.get("/api/v1/models/qwen2.5%3A7b/config").json()
    assert got["keep_alive"] == "1h"
    # 空指定でクリア → 既定へ
    admin_client.put("/api/v1/models/qwen2.5%3A7b/config", json={"keep_alive": "", "idle_exclude": False}, headers=CSRF_HEADERS)
    assert ollama.get_model_config("qwen2.5:7b") == {}
    assert ollama.effective_keep_alive("qwen2.5:7b") == ollama.get_settings()["default_keep_alive"]


def test_model_options_typed_and_effective(admin_client):
    """生成/ロードパラメータが型検証され、effective_options が options だけ返す。"""
    from app.models_mgmt import ollama

    admin_client.put(
        "/api/v1/models/m%3Atest/config",
        json={"num_ctx": "8192", "temperature": "0.3", "top_k": 40, "num_predict": -1,
              "keep_alive": "1h", "idle_exclude": True, "bogus": 5},
        headers=CSRF_HEADERS,
    )
    cfg = ollama.get_model_config("m:test")
    assert cfg["num_ctx"] == 8192 and isinstance(cfg["num_ctx"], int)  # 文字列→int
    assert cfg["temperature"] == 0.3 and isinstance(cfg["temperature"], float)
    assert "bogus" not in cfg  # 未許可キーは無視
    opts = ollama.effective_options("m:test")
    assert opts == {"num_ctx": 8192, "temperature": 0.3, "top_k": 40, "num_predict": -1}
    assert "keep_alive" not in opts and "idle_exclude" not in opts  # 運用フラグは options に含めない
    # 掃除
    from app.models_mgmt.ollama import _settings_path
    import json as _json
    s = ollama.get_settings(); s["model_configs"].pop("m:test", None)
    _settings_path().write_text(_json.dumps(s))


def test_kv_cache_settings_and_env(admin_client):
    from app.models_mgmt import ollama

    r = admin_client.put("/api/v1/models/settings",
                         json={"kv_cache_type": "q8_0", "flash_attention": True}, headers=CSRF_HEADERS)
    assert r.status_code == 200
    assert ollama.get_settings()["kv_cache_type"] == "q8_0"
    # 不正な値は 422
    r = admin_client.put("/api/v1/models/settings", json={"kv_cache_type": "q2_0"}, headers=CSRF_HEADERS)
    assert r.status_code == 422
    # 診断 API は dict を返す（systemctl 不在でも落ちない）
    env = admin_client.get("/api/v1/models/ollama-env").json()
    assert set(env) >= {"flash_attention", "kv_cache_type"}
    spec = admin_client.get("/api/v1/models/options-spec").json()
    assert "num_ctx" in spec["int"] and "temperature" in spec["float"]
    # 既定へ戻す
    admin_client.put("/api/v1/models/settings", json={"kv_cache_type": "f16", "flash_attention": False}, headers=CSRF_HEADERS)


def test_think_normalize_and_config(admin_client):
    """think の正規化と個別設定の保存/クリア。"""
    from app.models_mgmt import ollama

    assert ollama.normalize_think("off") is False
    assert ollama.normalize_think("on") is True
    assert ollama.normalize_think("high") == "high"
    assert ollama.normalize_think("auto") is None
    assert ollama.normalize_think("") is None
    assert ollama.normalize_think("garbage") is None

    admin_client.put("/api/v1/models/r%3Ax/config", json={"think": "off"}, headers=CSRF_HEADERS)
    assert ollama.get_model_config("r:x")["think"] == "off"
    assert ollama.effective_think("r:x") is False
    # options には含めない（think はトップレベルパラメータ）
    assert "think" not in ollama.effective_options("r:x")
    # auto でクリア
    admin_client.put("/api/v1/models/r%3Ax/config", json={"think": "auto"}, headers=CSRF_HEADERS)
    assert ollama.get_model_config("r:x") == {}


def test_native_base_helper():
    from app.workflows.chat_router import _native_base

    assert _native_base("http://127.0.0.1:11434/v1") == "http://127.0.0.1:11434"
    assert _native_base("http://127.0.0.1:11434/v1/") == "http://127.0.0.1:11434"
    assert _native_base("http://example.com/openai") is None  # 非 /v1 は None
