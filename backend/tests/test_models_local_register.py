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
