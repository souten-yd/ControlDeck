"""HuggingFace からの GGUF 取得。

役割:
- repo 検索と、repo 内の GGUF 一覧（量子化バリアント）の提示
- 選んだファイルをモデルライブラリ（libraries.py）へダウンロード
- 完了後に llama.cpp の instance として登録（任意）

huggingface_hub には依存せず httpx だけで扱う。既存の role_presets の
ダウンロード処理を一般化したもの。
"""
from __future__ import annotations

import re
import shutil
from pathlib import Path

import httpx

HF_API = "https://huggingface.co/api"
HF_RESOLVE = "https://huggingface.co"
# 分割GGUF（model-00001-of-00003.gguf）。1ファイルでは使えないのでまとめて扱う。
_SHARD_RE = re.compile(r"^(?P<stem>.+?)-(?P<index>\d{5})-of-(?P<total>\d{5})\.gguf$", re.I)
# ダウンロード後に残しておく余白。書き込み中の他プロセスやメタデータ用。
_FREE_SPACE_RESERVE = 1 * 1024 * 1024 * 1024


class HfError(RuntimeError):
    pass


def _headers() -> dict[str, str]:
    """gated repo 用のトークンがあれば付ける。無ければ匿名。"""
    from app.models_mgmt import gateway  # 設定の置き場を共用する

    token = ""
    try:
        token = str(gateway._load().get("hf_token") or "")
        if token:
            from app.security.crypto import decrypt_text

            token = decrypt_text(token)
    except Exception:  # noqa: BLE001 - トークン不備で匿名アクセスまで止めない
        token = ""
    headers = {"User-Agent": "ControlDeck/1.0"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def set_token(token: str) -> None:
    """gated repo 用トークンを保存する（空で解除）。"""
    from app.models_mgmt import gateway
    from app.security.crypto import encrypt_text

    data = gateway._load()
    if token.strip():
        data["hf_token"] = encrypt_text(token.strip())
    else:
        data.pop("hf_token", None)
    gateway._save(data)


def has_token() -> bool:
    from app.models_mgmt import gateway

    return bool(gateway._load().get("hf_token"))


async def search_models(query: str, limit: int = 20) -> list[dict]:
    """GGUF を持つ repo を検索する。"""
    params = {"search": query, "filter": "gguf", "limit": str(limit),
              "sort": "downloads", "direction": "-1"}
    try:
        async with httpx.AsyncClient(timeout=20, headers=_headers()) as client:
            response = await client.get(f"{HF_API}/models", params=params)
    except httpx.HTTPError as exc:
        raise HfError(f"HuggingFace 検索に失敗しました: {exc}") from exc
    if response.status_code >= 400:
        raise HfError(f"HuggingFace 検索エラー ({response.status_code})")
    return [{
        "repo": item.get("id", ""),
        "downloads": item.get("downloads", 0),
        "likes": item.get("likes", 0),
        "gated": bool(item.get("gated")),
    } for item in response.json()]


async def list_repo_files(repo: str, revision: str = "main") -> list[dict]:
    """repo 内の GGUF を量子化バリアントとして並べる。

    分割GGUF は 1 グループにまとめ、合計サイズと必要な全ファイルを返す
    （1つだけ落としても使えないため）。
    """
    if not re.fullmatch(r"[\w.\-]+/[\w.\-]+", repo or ""):
        raise HfError("repo は owner/name の形式で指定してください")
    try:
        async with httpx.AsyncClient(timeout=30, headers=_headers()) as client:
            response = await client.get(
                f"{HF_API}/models/{repo}/tree/{revision}", params={"recursive": "1"},
            )
    except httpx.HTTPError as exc:
        raise HfError(f"ファイル一覧の取得に失敗しました: {exc}") from exc
    if response.status_code == 401:
        raise HfError("認証が必要な repo です。HuggingFace トークンを設定してください")
    if response.status_code == 404:
        raise HfError("repo が見つかりません")
    if response.status_code >= 400:
        raise HfError(f"ファイル一覧の取得エラー ({response.status_code})")

    groups: dict[str, dict] = {}
    for entry in response.json():
        if entry.get("type") != "file":
            continue
        path = str(entry.get("path") or "")
        if not path.lower().endswith(".gguf"):
            continue
        size = int(entry.get("size") or 0)
        name = path.rsplit("/", 1)[-1]
        shard = _SHARD_RE.match(name)
        key = shard.group("stem") if shard else name
        group = groups.setdefault(key, {
            "name": key, "files": [], "size": 0,
            "sharded": bool(shard), "shard_total": int(shard.group("total")) if shard else 1,
        })
        group["files"].append(path)
        group["size"] += size
    for group in groups.values():
        group["files"].sort()
        group["complete"] = len(group["files"]) == group["shard_total"]
    return sorted(groups.values(), key=lambda g: g["name"].lower())


def _ensure_free_space(destination: Path, expected_bytes: int) -> None:
    """入らないものを落とし始めない（途中で詰まると中途半端なファイルが残る）。"""
    try:
        free = shutil.disk_usage(destination).free
    except OSError as exc:
        raise HfError(f"保存先を確認できません: {exc}") from exc
    if expected_bytes > max(0, free - _FREE_SPACE_RESERVE):
        raise HfError(
            f"空き容量が足りません（必要 {expected_bytes / 1e9:.1f}GB / "
            f"空き {free / 1e9:.1f}GB）"
        )


async def _download_file(job, client: httpx.AsyncClient, url: str, destination: Path,
                        *, label: str, done_bytes: int, total_bytes: int) -> int:
    """1ファイルをレジューム付きで取得する。"""
    temp = destination.with_suffix(destination.suffix + ".part")
    existing = temp.stat().st_size if temp.exists() else 0
    headers = {"Range": f"bytes={existing}-"} if existing else {}
    mode = "ab" if existing else "wb"
    async with client.stream("GET", url, headers=headers) as response:
        if response.status_code == 416:  # 既に全部取得済み
            temp.replace(destination)
            return destination.stat().st_size
        if response.status_code >= 400:
            raise HfError(f"ダウンロード失敗 ({response.status_code}): {label}")
        if existing and response.status_code != 206:
            # レンジ非対応なら最初から取り直す
            existing, mode = 0, "wb"
        received = existing
        with temp.open(mode) as target:
            async for chunk in response.aiter_bytes(1024 * 1024):
                target.write(chunk)
                received += len(chunk)
                job.set_progress(f"ダウンロード中: {label}", done_bytes + received, total_bytes or None)
    temp.replace(destination)
    return received


async def download(job, repo: str, files: list[str], *, library_id: str = "",
                   revision: str = "main", expected_bytes: int = 0,
                   register: dict | None = None) -> dict:
    """GGUF をライブラリへ取得し、必要なら instance として登録する。"""
    from app.models_mgmt import libraries

    if not files:
        raise HfError("ダウンロードするファイルがありません")
    root = (libraries.library_path(library_id, create=True) if library_id
            else libraries.default_models_dir())
    destination_dir = root / repo.replace("/", "--")
    destination_dir.mkdir(parents=True, exist_ok=True)
    if expected_bytes:
        _ensure_free_space(destination_dir, expected_bytes)

    saved: list[str] = []
    pending: list[tuple[str, Path]] = []
    done = 0
    for path in files:
        target = destination_dir / path.rsplit("/", 1)[-1]
        saved.append(str(target))
        if target.exists():
            done += target.stat().st_size
            job.log(f"既にあります: {target.name}")
        else:
            pending.append((path, target))

    # 全部揃っていれば接続もしない（再取得も余計なセッションも避ける）
    if pending:
        async with httpx.AsyncClient(timeout=None, follow_redirects=True,
                                     headers=_headers()) as client:
            for path, target in pending:
                url = f"{HF_RESOLVE}/{repo}/resolve/{revision}/{path}"
                try:
                    done += await _download_file(
                        job, client, url, target, label=target.name,
                        done_bytes=done, total_bytes=expected_bytes,
                    )
                finally:
                    target.with_suffix(target.suffix + ".part").unlink(missing_ok=True)

    result = {"repo": repo, "files": saved, "registered": ""}
    if register and saved:
        from app.models_mgmt import llama

        alias = str(register.get("alias") or "").strip()
        if alias:
            job.set_progress("モデルを登録中", 1, 1)
            patch = {"alias": alias, "model_path": saved[0]}
            for key in ("role", "endpoint_id", "port", "ctx_size", "n_parallel"):
                if register.get(key) is not None:
                    patch[key] = register[key]
            llama.save_instance(alias, patch)
            result["registered"] = alias
    job.set_progress("完了", 1, 1)
    return result
