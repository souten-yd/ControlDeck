"""ファイルマネージャーのサービス層。

すべてのパスは realpath 正規化 + 許可ルート検証（symlink 脱出防止）を通す。
拒否リスト（~/.ssh 等）は許可ルート内でもブロックする。
"""
from __future__ import annotations

import json
import os
import shutil
import time
import uuid
from pathlib import Path

from app.config import data_dir, get_config
from app.security.paths import is_within, normalize


class FileAccessError(PermissionError):
    pass


def _deny_roots() -> list[Path]:
    home = Path.home()
    return [
        home / ".ssh",
        home / ".gnupg",
        home / ".pki",
        home / ".mozilla",
        home / ".config" / "google-chrome",
        home / ".config" / "chromium",
        data_dir() / "secret.key",
        data_dir() / "control-deck.db",
    ]


def allowed_roots() -> list[Path]:
    return [normalize(r) for r in get_config().files.allowed_roots]


def resolve(path: str, *, must_exist: bool = True) -> Path:
    """パスを検証して返す。新規作成対象は親ディレクトリで検証する。"""
    roots = allowed_roots()
    if not roots:
        raise FileAccessError("ファイルアクセスが設定されていません（files.allowed_roots が空です）")
    p = Path(path).expanduser()
    if not p.is_absolute():
        raise FileAccessError("絶対パスで指定してください")
    if must_exist:
        resolved = normalize(str(p))
        if not resolved.exists():
            raise FileNotFoundError(f"存在しません: {path}")
    else:
        # 新規パス: 親を実体解決し、ファイル名を連結（ファイル名に .. を含めない）
        if p.name in ("", ".", ".."):
            raise FileAccessError("不正なファイル名です")
        resolved = normalize(str(p.parent)) / p.name
    if not any(is_within(resolved, root) for root in roots):
        raise FileAccessError(f"許可されたディレクトリの外です: {resolved}")
    for deny in _deny_roots():
        if is_within(resolved, deny) or resolved == deny:
            raise FileAccessError(f"アクセスが拒否されています: {resolved}")
    return resolved


def list_dir(path: str) -> dict:
    d = resolve(path)
    if not d.is_dir():
        raise NotADirectoryError(f"ディレクトリではありません: {path}")
    entries = []
    for child in d.iterdir():
        try:
            st = child.lstat()
            is_dir = child.is_dir()
            entries.append(
                {
                    "name": child.name,
                    "path": str(child),
                    "is_dir": is_dir,
                    "is_symlink": child.is_symlink(),
                    "size": 0 if is_dir else st.st_size,
                    "mtime": st.st_mtime,
                    "hidden": child.name.startswith("."),
                }
            )
        except OSError:
            continue
    entries.sort(key=lambda e: (not e["is_dir"], e["name"].lower()))
    return {"path": str(d), "entries": entries}


def file_info(path: str) -> dict:
    p = resolve(path)
    st = p.stat()
    return {
        "name": p.name,
        "path": str(p),
        "is_dir": p.is_dir(),
        "size": st.st_size,
        "mtime": st.st_mtime,
        "mode": oct(st.st_mode & 0o7777),
    }


TEXT_MAX_BYTES = 5 * 1024 * 1024


def read_text(path: str) -> str:
    p = resolve(path)
    if p.stat().st_size > TEXT_MAX_BYTES:
        raise FileAccessError("ファイルが大きすぎます（テキスト編集は 5MB まで）")
    return p.read_text(encoding="utf-8", errors="replace")


def write_text(path: str, content: str) -> None:
    p = resolve(path, must_exist=False)
    if p.exists() and p.is_dir():
        raise IsADirectoryError(f"ディレクトリです: {path}")
    p.write_text(content, encoding="utf-8")


def make_directory(path: str) -> None:
    p = resolve(path, must_exist=False)
    p.mkdir(parents=False, exist_ok=False)


def rename(path: str, new_name: str) -> str:
    if "/" in new_name or new_name in ("", ".", ".."):
        raise FileAccessError("不正な名前です")
    src = resolve(path)
    dst = resolve(str(src.parent / new_name), must_exist=False)
    if dst.exists():
        raise FileExistsError(f"既に存在します: {new_name}")
    src.rename(dst)
    return str(dst)


def copy(src_path: str, dst_dir: str) -> str:
    src = resolve(src_path)
    dst_parent = resolve(dst_dir)
    if not dst_parent.is_dir():
        raise NotADirectoryError(f"コピー先がディレクトリではありません: {dst_dir}")
    dst = resolve(str(dst_parent / src.name), must_exist=False)
    if dst.exists():
        raise FileExistsError(f"コピー先に既に存在します: {src.name}")
    if src.is_dir():
        if is_within(dst, src):
            raise FileAccessError("フォルダを自身の中へコピーできません")
        shutil.copytree(src, dst, symlinks=True)
    else:
        shutil.copy2(src, dst)
    return str(dst)


def move(src_path: str, dst_dir: str) -> str:
    src = resolve(src_path)
    dst_parent = resolve(dst_dir)
    if not dst_parent.is_dir():
        raise NotADirectoryError(f"移動先がディレクトリではありません: {dst_dir}")
    dst = resolve(str(dst_parent / src.name), must_exist=False)
    if dst.exists():
        raise FileExistsError(f"移動先に既に存在します: {src.name}")
    if src.is_dir() and is_within(dst, src):
        raise FileAccessError("フォルダを自身の中へ移動できません")
    shutil.move(str(src), str(dst))
    return str(dst)


def delete(path: str) -> None:
    p = resolve(path)
    if any(p == root for root in allowed_roots()):
        raise FileAccessError("許可ルート自体は削除できません")
    if p.is_dir() and not p.is_symlink():
        shutil.rmtree(p)
    else:
        p.unlink()


def _trash_root() -> Path:
    root = (data_dir() / "trash").resolve()
    (root / "items").mkdir(parents=True, exist_ok=True)
    (root / "meta").mkdir(parents=True, exist_ok=True)
    return root


def _safe_id(value: str) -> str:
    try:
        return str(uuid.UUID(value))
    except ValueError as e:
        raise FileAccessError("不正なIDです") from e


def _trash_meta(item_id: str) -> tuple[Path, dict]:
    safe = _safe_id(item_id)
    path = _trash_root() / "meta" / f"{safe}.json"
    if not path.is_file():
        raise FileNotFoundError("ごみ箱の項目が見つかりません")
    try:
        return path, json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        raise FileAccessError("ごみ箱メタデータが壊れています") from e


def move_to_trash(path: str, owner_user_id: int) -> dict:
    src = resolve(path)
    if any(src == root for root in allowed_roots()):
        raise FileAccessError("許可ルート自体は削除できません")
    item_id = str(uuid.uuid4())
    root = _trash_root()
    stored = root / "items" / item_id
    st = src.stat()
    meta = {
        "id": item_id, "name": src.name, "original_path": str(src),
        "is_dir": src.is_dir(), "size": st.st_size if src.is_file() else 0,
        "deleted_at": time.time(), "owner_user_id": owner_user_id,
    }
    shutil.move(str(src), str(stored))
    try:
        (root / "meta" / f"{item_id}.json").write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
    except OSError:
        shutil.move(str(stored), str(src))
        raise
    enforce_trash_limits()
    return meta


def list_trash(owner_user_id: int) -> list[dict]:
    purge_expired_trash()
    rows = []
    for path in (_trash_root() / "meta").glob("*.json"):
        try:
            meta = json.loads(path.read_text(encoding="utf-8"))
            if meta.get("owner_user_id") == owner_user_id and (_trash_root() / "items" / meta["id"]).exists():
                rows.append(meta)
        except (OSError, json.JSONDecodeError, KeyError):
            continue
    return sorted(rows, key=lambda x: x["deleted_at"], reverse=True)


def restore_trash(item_id: str, owner_user_id: int) -> str:
    meta_path, meta = _trash_meta(item_id)
    if meta.get("owner_user_id") != owner_user_id:
        raise FileNotFoundError("ごみ箱の項目が見つかりません")
    dst = resolve(meta["original_path"], must_exist=False)
    if dst.exists():
        raise FileExistsError(f"復元先に既に存在します: {dst}")
    if not dst.parent.is_dir():
        raise FileNotFoundError(f"復元先フォルダが存在しません: {dst.parent}")
    stored = _trash_root() / "items" / _safe_id(item_id)
    if not stored.exists():
        raise FileNotFoundError("ごみ箱の実体が見つかりません")
    shutil.move(str(stored), str(dst))
    meta_path.unlink(missing_ok=True)
    return str(dst)


def delete_trash(item_id: str, owner_user_id: int) -> None:
    meta_path, meta = _trash_meta(item_id)
    if meta.get("owner_user_id") != owner_user_id:
        raise FileNotFoundError("ごみ箱の項目が見つかりません")
    stored = _trash_root() / "items" / _safe_id(item_id)
    if stored.is_dir() and not stored.is_symlink():
        shutil.rmtree(stored)
    else:
        stored.unlink(missing_ok=True)
    meta_path.unlink(missing_ok=True)


def empty_trash(owner_user_id: int) -> int:
    rows = list_trash(owner_user_id)
    for row in rows:
        delete_trash(row["id"], owner_user_id)
    return len(rows)


def purge_expired_trash() -> int:
    cutoff = time.time() - get_config().files.trash_retention_days * 86400
    removed = 0
    for path in (_trash_root() / "meta").glob("*.json"):
        try:
            meta = json.loads(path.read_text(encoding="utf-8"))
            if float(meta.get("deleted_at", 0)) >= cutoff:
                continue
            stored = _trash_root() / "items" / _safe_id(meta["id"])
            if stored.is_dir() and not stored.is_symlink():
                shutil.rmtree(stored)
            else:
                stored.unlink(missing_ok=True)
            path.unlink(missing_ok=True)
            removed += 1
        except (OSError, ValueError, KeyError, json.JSONDecodeError):
            continue
    return removed


def enforce_trash_limits() -> None:
    limit = get_config().files.trash_max_size_gb * 1024**3
    records: list[tuple[float, Path, Path, int]] = []
    total = 0
    for meta_path in (_trash_root() / "meta").glob("*.json"):
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            stored = _trash_root() / "items" / _safe_id(meta["id"])
            size = stored.stat().st_size if stored.is_file() else sum(p.stat().st_size for p in stored.rglob("*") if p.is_file())
            records.append((float(meta["deleted_at"]), meta_path, stored, size))
            total += size
        except (OSError, ValueError, KeyError, json.JSONDecodeError):
            continue
    for _, meta_path, stored, size in sorted(records):
        if total <= limit:
            break
        if stored.is_dir() and not stored.is_symlink():
            shutil.rmtree(stored)
        else:
            stored.unlink(missing_ok=True)
        meta_path.unlink(missing_ok=True)
        total -= size


def _uploads_root() -> Path:
    root = (data_dir() / "uploads").resolve()
    root.mkdir(parents=True, exist_ok=True)
    return root


def create_upload(directory: str, filename: str, size: int, overwrite: bool, owner_user_id: int) -> dict:
    name = Path(filename).name
    if not name or name in (".", ".."):
        raise FileAccessError("不正なファイル名です")
    maximum = get_config().files.max_upload_size_gb * 1024**3
    if size < 0 or size > maximum:
        raise FileAccessError("アップロードサイズ上限を超えました")
    dst = resolve(str(Path(directory) / name), must_exist=False)
    if dst.exists() and not overwrite:
        raise FileExistsError(f"既に存在します: {name}")
    upload_id = str(uuid.uuid4())
    meta = {"id": upload_id, "destination": str(dst), "name": name, "size": size,
            "overwrite": overwrite, "owner_user_id": owner_user_id, "created_at": time.time()}
    root = _uploads_root()
    (root / f"{upload_id}.part").touch()
    (root / f"{upload_id}.json").write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
    return {**meta, "received": 0}


def upload_meta(upload_id: str, owner_user_id: int) -> tuple[Path, Path, dict]:
    safe = _safe_id(upload_id)
    root = _uploads_root()
    meta_path, part = root / f"{safe}.json", root / f"{safe}.part"
    if not meta_path.is_file() or not part.is_file():
        raise FileNotFoundError("アップロードが見つかりません")
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    if meta.get("owner_user_id") != owner_user_id:
        raise FileNotFoundError("アップロードが見つかりません")
    return meta_path, part, {**meta, "received": part.stat().st_size}


def append_upload(upload_id: str, owner_user_id: int, offset: int, chunk: bytes) -> dict:
    _, part, meta = upload_meta(upload_id, owner_user_id)
    if offset != meta["received"]:
        raise FileAccessError(f"offset が一致しません（期待値 {meta['received']}）")
    if len(chunk) > 8 * 1024 * 1024 or offset + len(chunk) > meta["size"]:
        raise FileAccessError("チャンクサイズまたは合計サイズが不正です")
    with part.open("ab") as f:
        f.write(chunk)
        f.flush()
        os.fsync(f.fileno())
    return {**meta, "received": offset + len(chunk)}


def complete_upload(upload_id: str, owner_user_id: int) -> dict:
    meta_path, part, meta = upload_meta(upload_id, owner_user_id)
    if meta["received"] != meta["size"]:
        raise FileAccessError(f"アップロードが未完了です（{meta['received']} / {meta['size']} bytes）")
    dst = resolve(meta["destination"], must_exist=False)
    if dst.exists() and not meta["overwrite"]:
        raise FileExistsError(f"既に存在します: {dst.name}")
    os.replace(part, dst)
    meta_path.unlink(missing_ok=True)
    return {"path": str(dst), "size": meta["size"]}


def cancel_upload(upload_id: str, owner_user_id: int) -> None:
    meta_path, part, _ = upload_meta(upload_id, owner_user_id)
    part.unlink(missing_ok=True)
    meta_path.unlink(missing_ok=True)
