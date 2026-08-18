"""モデルライブラリ: モデルファイルの保存先を複数のドライブに持てるようにする。

保存先を `data_dir/models/gguf` 決め打ちにせず、実機のボリュームを検出して
ユーザーが選んだ場所へ置けるようにする。参照（スキャン）・ダウンロード先・削除の
すべてがライブラリ単位で動く。

ボリュームはマウント名やデバイス名ではなく **UUID** で参照する。マウント名は
ユーザーが変えられ、`/dev/nvme0n1p1` のようなデバイス名もハードウェア構成で変わるため。
UUID のボリュームが現在マウントされていなければ「未接続」として扱い、
system ドライブへ暗黙にフォールバックしない（マウント漏れで / を埋める事故を防ぐ）。
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

from app.config import data_dir

# ネットワーク越し・読み取り専用・擬似ファイルシステムはモデル置き場にしない。
_EXCLUDED_FSTYPES = {
    "squashfs", "iso9660", "udf", "cifs", "smb3", "nfs", "nfs4",
    "fuse.sshfs", "fuse.rclone", "overlay", "tmpfs", "devtmpfs",
}
BUILTIN_LIBRARY_ID = "builtin"


class LibraryError(RuntimeError):
    pass


def detect_volumes() -> list[dict]:
    """モデル置き場の候補になるボリュームを列挙する。

    `lsblk` は util-linux 同梱で Ubuntu には標準で入っており、root も不要。
    `-e7` で snap の loop デバイスを除外する。
    """
    try:
        raw = subprocess.run(
            ["lsblk", "-J", "-b", "-e7", "-o",
             "NAME,PATH,SIZE,TYPE,TRAN,ROTA,FSTYPE,UUID,MOUNTPOINT,MODEL"],
            capture_output=True, text=True, timeout=10, check=False,
        )
        tree = json.loads(raw.stdout or "{}").get("blockdevices", [])
    except (OSError, ValueError, subprocess.SubprocessError):
        return []

    volumes: list[dict] = []

    def walk(node: dict, parent: dict | None) -> None:
        for child in node.get("children") or []:
            walk(child, node)
        mountpoint = node.get("mountpoint")
        fstype = str(node.get("fstype") or "")
        uuid = str(node.get("uuid") or "")
        if not mountpoint or not fstype or fstype in _EXCLUDED_FSTYPES:
            return
        if not uuid:  # UUID が無いと安定参照できないので候補にしない
            return
        # ブート領域やスナップのマウントはモデル置き場にならないので候補から外す。
        if any(mountpoint == p or mountpoint.startswith(p + "/")
               for p in ("/boot", "/efi", "/snap", "/var/snap")):
            return
        # transport / model はディスク側（親）にしか出ないことがある
        transport = str(node.get("tran") or (parent or {}).get("tran") or "")
        model = str(node.get("model") or (parent or {}).get("model") or "").strip()
        rotational = node.get("rota")
        if rotational is None:
            rotational = (parent or {}).get("rota")
        try:
            usage = shutil.disk_usage(mountpoint)
        except OSError:
            return
        volumes.append({
            "uuid": uuid,
            "device": str(node.get("path") or ""),
            "mountpoint": mountpoint,
            "fstype": fstype,
            "transport": transport,
            "model": model,
            "rotational": bool(rotational),
            "total_bytes": usage.total,
            "free_bytes": usage.free,
            "writable": os.access(mountpoint, os.W_OK),
            # / は OS 用。既定候補から外し、UI で注意を出すために印を付ける。
            "is_system": mountpoint == "/",
        })

    for device in tree:
        walk(device, None)
    volumes.sort(key=lambda v: (v["is_system"], -v["free_bytes"]))
    return volumes


def mountpoint_for(volume_uuid: str) -> str | None:
    """UUID から現在のマウントポイントを引く。未マウントなら None。"""
    if not volume_uuid:
        return None
    for volume in detect_volumes():
        if volume["uuid"] == volume_uuid:
            return volume["mountpoint"]
    return None


def _builtin_library() -> dict:
    """未設定環境の既定ライブラリ（従来の保存先）。"""
    return {
        "id": BUILTIN_LIBRARY_ID,
        "label": "内蔵（既定）",
        "volume_uuid": "",
        "subpath": "",
        "path": str(data_dir() / "models" / "gguf"),
        "default": True,
    }


def _configured() -> list[dict]:
    from app.models_mgmt.runtime_policy import get_policy

    entries = [item.model_dump() for item in get_policy().model_libraries]
    return entries or [_builtin_library()]


def _resolve_path(entry: dict) -> tuple[str, bool]:
    """(実効パス, マウント済みか) を返す。

    volume_uuid 指定のライブラリは、そのボリュームが未マウントなら
    パスを解決せず未接続として返す。ここで / 側へ落ちないことが重要。
    """
    uuid = str(entry.get("volume_uuid") or "")
    if uuid:
        mount = mountpoint_for(uuid)
        if mount is None:
            return "", False
        subpath = str(entry.get("subpath") or "").strip("/")
        return str(Path(mount) / subpath) if subpath else mount, True
    path = str(entry.get("path") or "")
    return path, bool(path)


def library_path(library_id: str, *, create: bool = False) -> Path:
    """ライブラリの実効パス。未接続・未知の ID は LibraryError。"""
    for entry in _configured():
        if entry["id"] != library_id:
            continue
        resolved, mounted = _resolve_path(entry)
        if not mounted or not resolved:
            raise LibraryError(
                f"ライブラリ '{entry.get('label') or library_id}' のドライブが接続されていません"
            )
        path = Path(resolved)
        if create:
            path.mkdir(parents=True, exist_ok=True)
        return path
    raise LibraryError("モデルライブラリが見つかりません")


def default_library_id() -> str:
    entries = _configured()
    for entry in entries:
        if entry.get("default"):
            resolved, mounted = _resolve_path(entry)
            if mounted and resolved:
                return str(entry["id"])
    # 既定が未接続なら、接続済みで最も空きの大きいものへ退避する
    best, best_free = "", -1
    for entry in entries:
        resolved, mounted = _resolve_path(entry)
        if not mounted or not resolved:
            continue
        try:
            free = shutil.disk_usage(resolved if Path(resolved).exists() else Path(resolved).parent).free
        except OSError:
            continue
        if free > best_free:
            best, best_free = str(entry["id"]), free
    if not best:
        raise LibraryError("利用できるモデルライブラリがありません（ドライブ未接続）")
    return best


def default_models_dir() -> Path:
    """ダウンロード等の既定保存先。"""
    return library_path(default_library_id(), create=True)


def _referenced_paths() -> dict[str, list[str]]:
    """GGUF パス → それを使っている instance alias の一覧。"""
    from app.models_mgmt import llama

    used: dict[str, list[str]] = {}
    for item in llama.list_instances():
        for key in ("model_path", "mmproj_path"):
            value = str(item.get(key) or "")
            if value:
                used.setdefault(value, []).append(str(item["alias"]))
    return used


def scan_library(library_id: str) -> dict:
    """ライブラリ内の GGUF を列挙し、登録済み／未登録（孤児）を仕分ける。"""
    from app.models_mgmt import ollama

    root = library_path(library_id)
    if not root.is_dir():
        return {"id": library_id, "path": str(root), "files": []}
    used = _referenced_paths()
    files = []
    for found in ollama.scan_gguf(str(root)):
        aliases = used.get(found["path"], [])
        files.append({
            **found,
            "used_by": aliases,
            "registered": bool(aliases),
            "suggest_alias": ollama.suggest_model_name(found["name"]),
        })
    files.sort(key=lambda f: (f["registered"], f["name"].lower()))
    return {"id": library_id, "path": str(root), "files": files}


def list_libraries() -> list[dict]:
    """UI 表示用に、各ライブラリの実効パス・接続状態・容量を返す。"""
    from app.models_mgmt import ollama

    used = _referenced_paths()
    result = []
    for entry in _configured():
        resolved, mounted = _resolve_path(entry)
        item = {
            **entry,
            "path": resolved,
            "mounted": mounted,
            "exists": bool(resolved) and Path(resolved).is_dir(),
            "total_bytes": None, "free_bytes": None,
            "gguf_count": 0, "gguf_bytes": 0, "orphan_count": 0,
        }
        if mounted and resolved:
            try:
                usage = shutil.disk_usage(resolved if Path(resolved).exists() else Path(resolved).parent)
                item["total_bytes"], item["free_bytes"] = usage.total, usage.free
            except OSError:
                pass
            if item["exists"]:
                try:
                    found = ollama.scan_gguf(resolved)
                except Exception:  # noqa: BLE001 - 一覧表示をスキャン失敗で落とさない
                    found = []
                item["gguf_count"] = len(found)
                item["gguf_bytes"] = sum(int(f.get("size") or 0) for f in found)
                item["orphan_count"] = sum(1 for f in found if not used.get(f["path"]))
        result.append(item)
    return result


def validate_entries(entries: list[dict]) -> list[dict]:
    """保存前の検証。パスは許可ルート内でなければならない。"""
    from app.files import service as files

    if len(entries) > 8:
        raise LibraryError("モデルライブラリは最大8件です")
    seen_ids: set[str] = set()
    for entry in entries:
        library_id = str(entry.get("id") or "")
        if library_id in seen_ids:
            raise LibraryError(f"ライブラリID '{library_id}' が重複しています")
        seen_ids.add(library_id)
        subpath = str(entry.get("subpath") or "")
        if ".." in Path(subpath).parts or subpath.startswith("/"):
            raise LibraryError("サブパスに .. や絶対パスは使えません")
        resolved, mounted = _resolve_path(entry)
        if not mounted or not resolved:
            # 未接続ドライブの登録自体は許す（後で挿せば使える）。パス検証は接続時に行う。
            continue
        try:
            files.resolve(resolved, must_exist=Path(resolved).exists())
        except (PermissionError, FileNotFoundError) as exc:
            raise LibraryError(
                f"'{resolved}' は許可されたディレクトリの外です。"
                f"config.yaml の files.allowed_roots に追加してください"
            ) from exc
    if entries and not any(e.get("default") for e in entries):
        entries[0]["default"] = True
    return entries
