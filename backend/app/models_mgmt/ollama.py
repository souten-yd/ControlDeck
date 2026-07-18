"""Ollama 管理サービス。

Ollama の REST API をラップし、モデルの一覧/詳細/取得(pull)/削除/ロード/アンロードと、
HuggingFace(GGUF) 検索、アイドル自動アンロードを提供する。

- 呼び出し時オートロード: Ollama ネイティブ（推論 API を叩くと自動ロード）
- アイドル自動アンロード: 本モジュールのループが /api/ps を監視し、
  idle_unload_minutes 経過したモデルへ keep_alive=0 を送って解放する
"""
from __future__ import annotations

import asyncio
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


# Ollama の /api options として渡せる生成/ロードパラメータ（モデル個別設定で保持）。
# int / float を型で分けてバリデーションする。
OPT_INT = {
    "num_ctx",       # コンテキスト長
    "num_predict",   # 出力トークン上限（-1=無制限, -2=コンテキストまで）
    "num_gpu",       # GPU にオフロードする層数（-1=自動で全部）
    "num_batch",     # バッチサイズ
    "num_thread",    # CPU スレッド数
    "num_keep",      # 先頭で保持するトークン数
    "top_k",
    "repeat_last_n",
    "seed",
    "mirostat",      # 0=無効 / 1 / 2
}
OPT_FLOAT = {
    "temperature",
    "top_p",
    "min_p",
    "typical_p",
    "repeat_penalty",
    "presence_penalty",
    "frequency_penalty",
    "mirostat_tau",
    "mirostat_eta",
}
OPT_KEYS = OPT_INT | OPT_FLOAT
# think（推論表示）の指定値。off/on はブール、low/medium/high/max はレベル
THINK_VALUES = ("off", "on", "low", "medium", "high", "max")

# モデル個別設定として保存できる全キー（options + 運用フラグ + think）
MODEL_CONFIG_KEYS = OPT_KEYS | {"keep_alive", "idle_exclude", "think", "deep_research_num_ctx", "vlm_enabled"}


def normalize_think(value) -> bool | str | None:
    """think 設定値を Ollama API 用に正規化する。

    None/""/"auto" → None（送らない＝モデル既定の自動）
    "off"/"false" → False、"on"/"true" → True、level 文字列 → そのまま
    """
    if value in (None, "", "auto"):
        return None
    v = str(value).lower()
    if v in ("off", "false", "0"):
        return False
    if v in ("on", "true", "1"):
        return True
    if v in ("low", "medium", "high", "max"):
        return v
    return None


def effective_think(model: str) -> bool | str | None:
    """モデル個別 think 設定を正規化して返す。"""
    return normalize_think(get_model_config(model).get("think"))

# KV キャッシュ量子化の選択肢（サーバー全体・環境変数）
KV_CACHE_TYPES = ("f16", "q8_0", "q4_0")

DEFAULT_SETTINGS = {
    "base_url": DEFAULT_BASE_URL,
    "idle_unload_enabled": False,
    "idle_unload_minutes": 30,
    "default_keep_alive": "30m",  # ロード時の既定保持時間（大型モデルの都度ロードを防ぐ）
    "default_model": "",          # LLM ノードの既定に使える
    # モデル別の詳細設定 {"モデル名": {"keep_alive": "1h", "idle_exclude": true, "num_ctx": 8192, ...}}
    "model_configs": {},
    # サーバー全体（Ollama 環境変数で反映。適用は systemctl edit ollama + 再起動）
    "kv_cache_type": "f16",       # f16 / q8_0 / q4_0（q系は flash_attention 必須）
    "flash_attention": False,     # KV キャッシュ量子化を効かせるには true
}


def get_model_config(model: str) -> dict:
    """モデル個別設定（未設定なら空 dict）。"""
    cfgs = get_settings().get("model_configs") or {}
    return dict(cfgs.get(model, {}))


def set_model_config(model: str, patch: dict) -> dict:
    """モデル個別設定を更新する。MODEL_CONFIG_KEYS のみ許可・型検証。"""
    s = get_settings()
    cfgs = dict(s.get("model_configs") or {})
    cur = dict(cfgs.get(model, {}))
    for k, v in patch.items():
        if k not in MODEL_CONFIG_KEYS:
            continue
        # think は "off" も有効値。"auto"/空でクリア
        if k == "think":
            if normalize_think(v) is None:
                cur.pop(k, None)
            else:
                cur[k] = str(v).lower()
            continue
        # 空/None/False はクリア（既定へ戻す）
        if v in (None, "", False):
            cur.pop(k, None)
            continue
        if k == "deep_research_num_ctx":
            try:
                tokens = int(v)
            except (ValueError, TypeError):
                continue
            if 0 < tokens <= 1_048_576:
                cur[k] = tokens
            continue
        if k in OPT_INT:
            try:
                cur[k] = int(v)
            except (ValueError, TypeError):
                continue
        elif k in OPT_FLOAT:
            try:
                cur[k] = float(v)
            except (ValueError, TypeError):
                continue
        else:  # keep_alive(str) / idle_exclude(bool)
            cur[k] = v
    if cur:
        cfgs[model] = cur
    else:
        cfgs.pop(model, None)
    s["model_configs"] = cfgs
    _settings_path().write_text(json.dumps(s, ensure_ascii=False, indent=2))
    return cur


def effective_keep_alive(model: str) -> str | int:
    """モデル個別 keep_alive → なければ既定。"""
    cfg = get_model_config(model)
    return cfg.get("keep_alive") or get_settings().get("default_keep_alive", "30m")


def effective_options(model: str) -> dict:
    """モデル個別設定から Ollama の options dict を組み立てる（運用フラグは除く）。"""
    cfg = get_model_config(model)
    return {k: v for k, v in cfg.items() if k in OPT_KEYS}


def runtime_env() -> dict:
    """稼働中 Ollama サービスの KV キャッシュ関連環境変数を読む（診断・UI 表示用）。

    root 権限なしで読める systemctl show を使う。取得できなければ空。
    """
    import shutil
    import subprocess

    out = {"flash_attention": None, "kv_cache_type": None, "source": ""}
    if not shutil.which("systemctl"):
        return out
    try:
        r = subprocess.run(
            ["systemctl", "show", "ollama", "-p", "Environment"],
            capture_output=True, text=True, timeout=5,
        )
        env = r.stdout.strip()
        for token in env.replace("Environment=", "").split():
            if token.startswith("OLLAMA_FLASH_ATTENTION="):
                out["flash_attention"] = token.split("=", 1)[1] in ("1", "true", "True")
            elif token.startswith("OLLAMA_KV_CACHE_TYPE="):
                out["kv_cache_type"] = token.split("=", 1)[1]
        out["source"] = "systemd"
    except Exception:
        pass
    return out


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
    running_items = await running_models()
    running_by_name: dict[str, dict] = {}
    running_by_digest: dict[str, dict] = {}
    for item in running_items:
        for field in ("name", "model"):
            key = normalize_model_name(item.get(field))
            if key:
                running_by_name[key] = item
        digest = str(item.get("digest") or "").strip().lower()
        if digest:
            running_by_digest[digest] = item
    out = []
    for m in models:
        details = m.get("details", {})
        name = str(m.get("name") or m.get("model") or "")
        digest = str(m.get("digest") or "").strip().lower()
        active = running_by_name.get(normalize_model_name(name))
        if active is None and digest:
            active = running_by_digest.get(digest)
        out.append({
            "name": name,
            "digest": m.get("digest", ""),
            "size": m.get("size", 0),
            "modified_at": m.get("modified_at", ""),
            "family": details.get("family", ""),
            "parameter_size": details.get("parameter_size", ""),
            "quantization": details.get("quantization_level", ""),
            "loaded": active is not None,
            "expires_at": (active or {}).get("expires_at"),
            "vram": (active or {}).get("size_vram"),
        })
    return out


def normalize_model_name(value: object) -> str:
    """Ollamaのname/model表記を比較用に正規化する（省略tagはlatest）。"""
    name = str(value or "").strip().lower()
    if not name:
        return ""
    leaf = name.rsplit("/", 1)[-1]
    return name if ":" in leaf else f"{name}:latest"


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


async def load(model: str, keep_alive: str | int | None = None, options: dict | None = None) -> dict:
    from app.models_mgmt.runtime_policy import ensure_gpu_profile

    await asyncio.to_thread(ensure_gpu_profile)
    ka = keep_alive if keep_alive is not None else effective_keep_alive(model)
    opts = options if options is not None else effective_options(model)
    # 空プロンプトの generate でモデルだけロードする（num_ctx 等はここで確定する）
    payload: dict = {"model": model, "keep_alive": ka}
    if opts:
        payload["options"] = opts
    r = await _post("/api/generate", payload, timeout=180)
    if r.status_code >= 400:
        raise OllamaError(f"ロードに失敗しました ({r.status_code}): {r.text[:200]}")
    return {"model": model, "loaded": True, "keep_alive": ka, "options": opts}


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
            # アイドルアンロードは⚙️共通設定（runtime policy）を正とする。
            # llama.cppのidle loopと同じ設定を読み、両ランタイムで挙動を揃える。
            from app.models_mgmt.runtime_policy import get_policy

            policy = get_policy()
            if not policy.idle_unload_enabled:
                _activity.clear()
                continue
            limit = max(1, int(policy.idle_unload_minutes)) * 60
            now = time.time()
            running = await running_models()
            current = {m["name"] for m in running}
            for name in list(_activity):
                if name not in current:
                    _activity.pop(name, None)
            for m in running:
                name = m["name"]
                # モデル個別設定でアイドルアンロード除外なら対象外（常駐させる）
                if get_model_config(name).get("idle_exclude"):
                    _activity.pop(name, None)
                    continue
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
