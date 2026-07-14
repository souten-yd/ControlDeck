"""Ollama 管理サービス。

Ollama の REST API をラップし、モデルの一覧/詳細/取得(pull)/削除/ロード/アンロードと、
HuggingFace(GGUF) 検索、アイドル自動アンロードを提供する。

- 呼び出し時オートロード: Ollama ネイティブ（推論 API を叩くと自動ロード）
- アイドル自動アンロード: 本モジュールのループが /api/ps を監視し、
  idle_unload_minutes 経過したモデルへ keep_alive=0 を送って解放する
"""
from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import AsyncIterator

import httpx

from app.config import data_dir

logger = logging.getLogger("control_deck.models")

DEFAULT_BASE_URL = "http://127.0.0.1:11434"


class OllamaError(Exception):
    pass


def _settings_path() -> Path:
    return data_dir() / "ollama-settings.json"


DEFAULT_SETTINGS = {
    "base_url": DEFAULT_BASE_URL,
    "idle_unload_enabled": False,
    "idle_unload_minutes": 30,
    "default_keep_alive": "5m",  # ロード時の既定保持時間
    "default_model": "",         # LLM ノードの既定に使える
}


def get_settings() -> dict:
    s = dict(DEFAULT_SETTINGS)
    p = _settings_path()
    if p.exists():
        try:
            s.update(json.loads(p.read_text()))
        except (json.JSONDecodeError, OSError):
            pass
    return s


def save_settings(patch: dict) -> dict:
    s = get_settings()
    s.update({k: v for k, v in patch.items() if k in DEFAULT_SETTINGS})
    _settings_path().write_text(json.dumps(s, ensure_ascii=False, indent=2))
    return s


def base_url() -> str:
    return str(get_settings().get("base_url") or DEFAULT_BASE_URL).rstrip("/")


async def _get(path: str, timeout: float = 15) -> httpx.Response:
    async with httpx.AsyncClient(timeout=timeout) as client:
        return await client.get(base_url() + path)


async def _post(path: str, payload: dict, timeout: float = 60) -> httpx.Response:
    async with httpx.AsyncClient(timeout=timeout) as client:
        return await client.post(base_url() + path, json=payload)


async def status() -> dict:
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            r = await client.get(base_url() + "/api/version")
        if r.status_code == 200:
            return {"available": True, "version": r.json().get("version", ""), "base_url": base_url()}
    except httpx.HTTPError:
        pass
    return {"available": False, "version": "", "base_url": base_url()}


async def list_models() -> list[dict]:
    try:
        r = await _get("/api/tags")
    except httpx.HTTPError as e:
        raise OllamaError(f"Ollama に接続できません: {e}")
    if r.status_code >= 400:
        raise OllamaError(f"一覧取得に失敗しました ({r.status_code})")
    models = r.json().get("models", [])
    running = {m["name"]: m for m in await running_models()}
    out = []
    for m in models:
        details = m.get("details", {})
        out.append({
            "name": m.get("name", ""),
            "size": m.get("size", 0),
            "modified_at": m.get("modified_at", ""),
            "family": details.get("family", ""),
            "parameter_size": details.get("parameter_size", ""),
            "quantization": details.get("quantization_level", ""),
            "loaded": m.get("name") in running,
            "expires_at": running.get(m.get("name"), {}).get("expires_at"),
            "vram": running.get(m.get("name"), {}).get("size_vram"),
        })
    return out


async def running_models() -> list[dict]:
    try:
        r = await _get("/api/ps")
    except httpx.HTTPError:
        return []
    if r.status_code >= 400:
        return []
    return r.json().get("models", [])


async def show(model: str) -> dict:
    r = await _post("/api/show", {"model": model})
    if r.status_code >= 400:
        raise OllamaError(f"詳細取得に失敗しました ({r.status_code})")
    d = r.json()
    info = d.get("model_info", {})
    ctx = next((v for k, v in info.items() if k.endswith(".context_length")), None)
    return {
        "model": model,
        "parameters": d.get("parameters", ""),
        "template": d.get("template", "")[:2000],
        "details": d.get("details", {}),
        "license": (d.get("license", "") or "")[:1000],
        "context_length": ctx,
        "capabilities": d.get("capabilities", []),
    }


async def delete(model: str) -> None:
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.request("DELETE", base_url() + "/api/delete", json={"model": model})
    if r.status_code >= 400:
        raise OllamaError(f"削除に失敗しました ({r.status_code}): {r.text[:200]}")


async def load(model: str, keep_alive: str | int | None = None) -> dict:
    ka = keep_alive if keep_alive is not None else get_settings().get("default_keep_alive", "5m")
    # 空プロンプトの generate でモデルだけロードする
    r = await _post("/api/generate", {"model": model, "keep_alive": ka}, timeout=120)
    if r.status_code >= 400:
        raise OllamaError(f"ロードに失敗しました ({r.status_code}): {r.text[:200]}")
    return {"model": model, "loaded": True, "keep_alive": ka}


async def unload(model: str) -> dict:
    r = await _post("/api/generate", {"model": model, "keep_alive": 0}, timeout=30)
    if r.status_code >= 400:
        raise OllamaError(f"アンロードに失敗しました ({r.status_code})")
    return {"model": model, "loaded": False}


async def pull_stream(model: str) -> AsyncIterator[dict]:
    """モデル取得の進捗を逐次 yield する。HuggingFace は hf.co/... 形式で pull 可能。"""
    async with httpx.AsyncClient(timeout=None) as client:
        async with client.stream("POST", base_url() + "/api/pull", json={"model": model, "stream": True}) as r:
            if r.status_code >= 400:
                text = await r.aread()
                raise OllamaError(f"取得に失敗しました ({r.status_code}): {text[:200]!r}")
            async for line in r.aiter_lines():
                if not line.strip():
                    continue
                try:
                    yield json.loads(line)
                except json.JSONDecodeError:
                    continue


async def hf_search(query: str, limit: int = 20) -> list[dict]:
    """HuggingFace の GGUF モデルを検索する（Ollama で pull 可能な hf.co/... を提示）。"""
    params = {"search": query, "filter": "gguf", "limit": str(limit), "sort": "downloads", "direction": "-1"}
    try:
        async with httpx.AsyncClient(timeout=20, headers={"User-Agent": "ControlDeck/1.0"}) as client:
            r = await client.get("https://huggingface.co/api/models", params=params)
    except httpx.HTTPError as e:
        raise OllamaError(f"HuggingFace 検索に失敗しました: {e}")
    if r.status_code >= 400:
        raise OllamaError(f"HuggingFace 検索エラー ({r.status_code})")
    out = []
    for m in r.json():
        repo = m.get("id", "")
        out.append({
            "repo": repo,
            "downloads": m.get("downloads", 0),
            "likes": m.get("likes", 0),
            "pull_hint": f"hf.co/{repo}",  # 量子化違いは :Q4_K_M 等で指定
        })
    return out


# ---- ローカル GGUF の登録（既存ダウンロードモデルの取り込み） ----

_CHUNK = 8 * 1024 * 1024
_SCAN_MAX_DEPTH = 3
_SCAN_MAX_FILES = 200


def scan_gguf(dir_path: str) -> list[dict]:
    """許可ルート配下のフォルダから GGUF ファイルを探す（深さ・件数制限あり）。"""
    from app.files import service as files_service

    root = files_service.resolve(dir_path)
    if not root.is_dir():
        raise OllamaError("フォルダを指定してください")
    out: list[dict] = []

    def walk(d: Path, depth: int) -> None:
        if depth > _SCAN_MAX_DEPTH or len(out) >= _SCAN_MAX_FILES:
            return
        try:
            entries = sorted(d.iterdir(), key=lambda p: p.name.lower())
        except OSError:
            return
        for p in entries:
            if len(out) >= _SCAN_MAX_FILES:
                return
            if p.is_symlink():
                continue
            if p.is_dir():
                walk(p, depth + 1)
            elif p.suffix.lower() == ".gguf":
                try:
                    out.append({"name": p.name, "path": str(p), "size": p.stat().st_size})
                except OSError:
                    continue

    walk(root, 0)
    return out


def suggest_model_name(filename: str) -> str:
    """GGUF ファイル名から Ollama モデル名の候補を作る。"""
    import re

    stem = Path(filename).stem
    name = re.sub(r"[^a-zA-Z0-9._-]+", "-", stem).strip("-._").lower()
    return name or "local-model"


async def register_gguf_stream(name: str, path: str) -> AsyncIterator[dict]:
    """ローカル GGUF を Ollama へ登録する（ハッシュ → blob 転送 → create）。

    進捗 {status, completed?, total?} を逐次 yield する。パスは files の
    許可ルート配下のみ（FilePicker と同じ制約）。
    """
    import asyncio
    import hashlib
    import re

    from app.files import service as files_service

    if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9._-]*(:[a-zA-Z0-9._-]+)?", name):
        raise OllamaError("モデル名が不正です（英数字と . _ - 、タグは : で指定）")
    p = files_service.resolve(path)
    if p.suffix.lower() != ".gguf" or not p.is_file():
        raise OllamaError("GGUF ファイルを指定してください")
    total = p.stat().st_size

    # 1. SHA-256（Ollama の blob ID）
    h = hashlib.sha256()
    done = 0
    with p.open("rb") as f:
        while True:
            chunk = await asyncio.to_thread(f.read, _CHUNK)
            if not chunk:
                break
            await asyncio.to_thread(h.update, chunk)
            done += len(chunk)
            yield {"status": "検証中（SHA-256 計算）", "completed": done, "total": total}
    digest = f"sha256:{h.hexdigest()}"

    async with httpx.AsyncClient(timeout=None) as client:
        # 2. blob が未登録なら転送
        head = await client.head(f"{base_url()}/api/blobs/{digest}")
        if head.status_code != 200:
            progress = {"sent": 0}

            async def body() -> AsyncIterator[bytes]:
                with p.open("rb") as f2:
                    while True:
                        c = await asyncio.to_thread(f2.read, _CHUNK)
                        if not c:
                            return
                        progress["sent"] += len(c)
                        yield c

            upload = asyncio.create_task(client.post(f"{base_url()}/api/blobs/{digest}", content=body()))
            while not upload.done():
                await asyncio.sleep(0.5)
                yield {"status": "Ollama へ転送中", "completed": progress["sent"], "total": total}
            r = upload.result()
            if r.status_code >= 400:
                raise OllamaError(f"転送に失敗しました ({r.status_code}): {r.text[:200]}")

        # 3. create（新 API: files に blob を紐付け）
        yield {"status": "モデルを作成中"}
        async with client.stream(
            "POST", base_url() + "/api/create",
            json={"model": name, "files": {p.name: digest}, "stream": True},
        ) as r:
            if r.status_code >= 400:
                text = await r.aread()
                raise OllamaError(f"作成に失敗しました ({r.status_code}): {text[:200]!r}")
            async for line in r.aiter_lines():
                if not line.strip():
                    continue
                try:
                    data = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if data.get("error"):
                    raise OllamaError(str(data["error"])[:300])
                yield data


# ---- アイドル自動アンロード ----
# モデルごとに (最終活動時刻, 前回観測した expires_at) を保持。
# Ollama は推論アクセスのたびに expires_at を先送りするため、その変化で活動を検知する。
_activity: dict[str, tuple[float, str]] = {}


async def idle_unload_loop() -> None:
    """設定が有効なら、一定時間 API 呼び出しの無いロード済みモデルをアンロードする。"""
    import asyncio

    while True:
        try:
            await asyncio.sleep(60)
            s = get_settings()
            if not s.get("idle_unload_enabled"):
                _activity.clear()
                continue
            limit = max(1, int(s.get("idle_unload_minutes", 30))) * 60
            now = time.time()
            running = await running_models()
            current = {m["name"] for m in running}
            for name in list(_activity):
                if name not in current:
                    _activity.pop(name, None)
            for m in running:
                name = m["name"]
                exp = str(m.get("expires_at", ""))
                last_active, prev_exp = _activity.get(name, (now, exp))
                if exp != prev_exp:  # expires_at が動いた = 直近で呼ばれた
                    last_active = now
                _activity[name] = (last_active, exp)
                if now - last_active >= limit:
                    try:
                        await unload(name)
                        logger.info("アイドル(%d分)のためモデルをアンロード: %s", limit // 60, name)
                        _activity.pop(name, None)
                    except OllamaError:
                        pass
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("idle_unload_loop error")
