"""ナレッジの保存先と保存対象の設定。

利用者の要望（2026-11-19）: 「ローカルに保存するもの、サーバーに保存するバックアップ、図表などを
分けて設定したい。保存場所と保存対象を分けて設定できれば柔軟にできる。将来は QNAP がこのデータを
読んで活用するアプリもありうるが、当面は Linux 単体で検証・開発する」。

- **保存先（target）**: 名前のついたディレクトリ。既定は ``local``（data_dir/knowledge、消せない）。
  NAS は OS でマウントしたディレクトリを保存先として登録する（NFS / SMB のマウントはこのアプリでは行わない）。
- **保存対象（category）**: db（分野ごと）・graphs・originals・figures・extractions を、それぞれどの保存先に置くか。
  backup は保存先のリストと世代数。
- SQLite（db・graphs）はネットワーク上のファイルシステムに置かない。ロックが効かず壊れるため、設定時に拒否する。
- 保存先には目印のファイル（``.controldeck-knowledge``）を置き、その ID が設定と一致するときだけ使う。
  NAS が外れてマウント先が空のディレクトリになっていても、そこへ黙って書かない。
- 保存先のパスはファイル管理の許可ルート（files.allowed_roots）か data_dir の下に限る（AGENTS.md 絶対ルール 3）。

設定は data_dir/knowledge-storage.json。保存先の中身は自己説明的にしておく（README.txt・manifest.json）。
QNAP など別のアプリが読む場合は、バックアップの世代（SQLite のスナップショット・ハッシュ名の blobs・manifest）を
読み取り専用で使う想定。
"""
from __future__ import annotations

import json
import os
import re
import shutil
import threading
import time
import uuid
from pathlib import Path

from app.config import data_dir

CONFIG_NAME = "knowledge-storage.json"
MARKER = ".controldeck-knowledge"
LOCAL = "local"
TARGET_RE = re.compile(r"^[a-z][a-z0-9_-]{0,31}$")

SHARDS = ("general", "patents", "standards", "news")
BLOB_CATEGORIES = ("originals", "figures", "extractions")
CATEGORIES = {
    "db": "検索用データベース（分野ごと。SQLite）",
    "graphs": "GraphRAG の三つ組（SQLite）",
    "originals": "原本（PDF など）",
    "figures": "図・表・ページの画像",
    "extractions": "抽出結果（OCR・レイアウトの JSON）",
}
SQLITE_CATEGORIES = ("db", "graphs")
ROLE_CATEGORY = {"original": "originals", "image": "figures", "figure": "figures", "table": "figures",
                 "page": "figures", "extraction": "extractions"}
NETWORK_FS = {"nfs", "nfs4", "cifs", "smb3", "smbfs", "fuse.sshfs", "9p", "ceph", "glusterfs",
              "fuse.glusterfs", "davfs", "fuse.davfs2", "afs", "fuse.rclone", "fuse.s3fs"}
DEFAULT_BACKUP_KEEP = 7

README = """ControlDeck ナレッジの保存先

このディレクトリは ControlDeck が管理しています。中身を手で書き換えないでください。

  .controldeck-knowledge   保存先の目印（ID）。消すと ControlDeck はここを使わなくなる
  {shard}.sqlite3          検索用データベース（general / patents / standards / news）。SQLite
  graphs/                  GraphRAG の三つ組（SQLite）
  blobs/ab/<sha256>        原本・図・抽出結果。名前は内容の SHA-256。書いたら変えない
  backups/<日時>/          バックアップの世代。manifest.json に各ファイルの SHA-256 と件数

別のアプリから読む場合は backups/ の世代を読み取り専用で使ってください（稼働中の .sqlite3 を直接開かない）。
"""


class StorageError(ValueError):
    pass


class StorageUnavailable(StorageError):
    pass


_lock = threading.Lock()
_cache: tuple[float, dict] | None = None


def config_path() -> Path:
    return data_dir() / CONFIG_NAME


def local_root() -> Path:
    return data_dir() / "knowledge"


def default_config() -> dict:
    return {
        "version": 1,
        "targets": {LOCAL: {"path": str(local_root()), "label": "この PC", "id": ""}},
        "placement": {"db": {shard: LOCAL for shard in SHARDS}, "graphs": LOCAL,
                      "originals": LOCAL, "figures": LOCAL, "extractions": LOCAL},
        "backup": {"targets": [], "keep": DEFAULT_BACKUP_KEEP,
                   "include": ["db", "graphs", "originals", "figures", "extractions"]},
    }


def load() -> dict:
    global _cache
    path = config_path()
    try:
        mtime = path.stat().st_mtime
    except FileNotFoundError:
        mtime = -1.0
    with _lock:
        if _cache is not None and _cache[0] == mtime:
            return json.loads(json.dumps(_cache[1]))
    cfg = default_config()
    if mtime >= 0:
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise StorageError(f"保存先の設定を読めません: {exc}") from exc
        cfg = _merge(cfg, raw)
    # 既定の保存先のパスは常に data_dir/knowledge（設定ファイルで変えさせない）
    cfg["targets"][LOCAL]["path"] = str(local_root())
    with _lock:
        _cache = (mtime, cfg)
    return json.loads(json.dumps(cfg))


def _merge(base: dict, raw: dict) -> dict:
    out = json.loads(json.dumps(base))
    for name, target in (raw.get("targets") or {}).items():
        if isinstance(target, dict):
            out["targets"][name] = {**out["targets"].get(name, {}), **target}
    placement = raw.get("placement") or {}
    if isinstance(placement.get("db"), dict):
        out["placement"]["db"].update(placement["db"])
    for category in ("graphs", *BLOB_CATEGORIES):
        if isinstance(placement.get(category), str):
            out["placement"][category] = placement[category]
    if isinstance(raw.get("backup"), dict):
        out["backup"].update(raw["backup"])
    return out


def _allowed_parents() -> list[Path]:
    from app.files.service import allowed_roots

    roots = [data_dir().resolve()]
    try:
        roots += [r.resolve() for r in allowed_roots()]
    except Exception:  # ファイル管理の設定が無い環境では data_dir だけ
        pass
    return roots


def _check_path(raw: str) -> Path:
    if not raw or not Path(raw).is_absolute():
        raise StorageError("保存先は絶対パスで指定してください")
    path = Path(raw).expanduser().resolve()
    if not any(path == root or root in path.parents for root in _allowed_parents()):
        raise StorageError("保存先はファイル管理の許可ルート（files.allowed_roots）か data_dir の下にしてください。"
                           "NAS はマウント先を許可ルートに加えてから登録します")
    return path


def validate(cfg: dict) -> dict:
    """設定を検査して正規化した形を返す。保存はしない。"""
    out = _merge(default_config(), cfg)
    out["targets"][LOCAL] = {**out["targets"][LOCAL], "path": str(local_root())}
    for name, target in out["targets"].items():
        if not TARGET_RE.match(name):
            raise StorageError(f"保存先の名前は英小文字で始まる英数・ハイフン・下線 32 文字まで: {name}")
        if name != LOCAL:
            target["path"] = str(_check_path(str(target.get("path", ""))))
        target["label"] = str(target.get("label", ""))[:64]
        target["id"] = str(target.get("id", ""))[:64]
    names = set(out["targets"])
    for shard in SHARDS:
        if out["placement"]["db"].get(shard) not in names:
            raise StorageError(f"db.{shard} の保存先がありません")
    for category in ("graphs", *BLOB_CATEGORIES):
        if out["placement"][category] not in names:
            raise StorageError(f"{category} の保存先がありません")
    for category, name in _sqlite_placements(out):
        info = filesystem(Path(out["targets"][name]["path"]))
        if info["network"]:
            raise StorageError(f"{category} は SQLite なので、ネットワーク上のファイルシステム（{info['fstype']}）に置けません")
    backup = out["backup"]
    backup["targets"] = [str(t) for t in backup.get("targets") or []]
    for name in backup["targets"]:
        if name not in names:
            raise StorageError(f"バックアップ先がありません: {name}")
    try:
        backup["keep"] = max(1, min(int(backup.get("keep", DEFAULT_BACKUP_KEEP)), 365))
    except (TypeError, ValueError) as exc:
        raise StorageError("世代数は 1〜365") from exc
    include = [c for c in backup.get("include") or [] if c in CATEGORIES]
    backup["include"] = include or list(CATEGORIES)
    return out


def _sqlite_placements(cfg: dict) -> list[tuple[str, str]]:
    return [(f"db.{shard}", cfg["placement"]["db"][shard]) for shard in SHARDS] + [("graphs", cfg["placement"]["graphs"])]


def save(cfg: dict) -> dict:
    global _cache
    out = validate(cfg)
    current = load()
    # 既に DB があるのに置き場所を変えるのは move_db でだけ行う（書き込み中に別の場所を見せない）
    for shard in SHARDS:
        old, new = current["placement"]["db"][shard], out["placement"]["db"][shard]
        if old != new and (target_root(old, require=False) / f"{shard}.sqlite3").exists():
            raise StorageError(f"db.{shard} は既にデータがあります。移すときは ./deck.sh knowledge-storage move-db を使ってください")
    path = config_path()
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    os.chmod(tmp, 0o600)
    tmp.replace(path)
    with _lock:
        _cache = None
    return load()


# ---- 保存先の状態 ----

def filesystem(path: Path) -> dict:
    """path を含むマウントのファイルシステム種別（/proc/self/mountinfo）。"""
    probe = path
    while not probe.exists() and probe != probe.parent:
        probe = probe.parent
    best, fstype, source = "", "", ""
    try:
        target = str(probe.resolve())
        with open("/proc/self/mountinfo", encoding="utf-8") as fh:
            for line in fh:
                left, _, right = line.partition(" - ")
                fields, rest = left.split(), right.split()
                if len(fields) < 5 or not rest:
                    continue
                mount = fields[4].replace("\\040", " ")
                if (target == mount or target.startswith(mount.rstrip("/") + "/")) and len(mount) >= len(best):
                    best, fstype, source = mount, rest[0], rest[1] if len(rest) > 1 else ""
    except OSError:
        pass
    return {"mount": best, "fstype": fstype, "network": fstype in NETWORK_FS, "source": source}


def _read_marker(root: Path) -> dict | None:
    try:
        return json.loads((root / MARKER).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def init_target(name: str) -> dict:
    """保存先に目印と説明を置いて使えるようにする。既に目印があればその ID を使う。"""
    cfg = load()
    target = cfg["targets"].get(name)
    if target is None:
        raise StorageError(f"保存先がありません: {name}")
    root = Path(target["path"]) if name == LOCAL else _check_path(target["path"])
    if name != LOCAL and not root.exists():
        raise StorageUnavailable(f"保存先のディレクトリがありません（マウントされていますか）: {root}")
    root.mkdir(parents=True, exist_ok=True)
    marker = _read_marker(root)
    if marker is None:
        marker = {"id": uuid.uuid4().hex, "created": time.time(), "name": name}
        (root / MARKER).write_text(json.dumps(marker), encoding="utf-8")
    (root / "README.txt").write_text(README, encoding="utf-8")
    if target.get("id") != marker["id"]:
        cfg["targets"][name]["id"] = marker["id"]
        _write_raw(cfg)
    return status(name)


def _write_raw(cfg: dict) -> None:
    global _cache
    path = config_path()
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(cfg, ensure_ascii=False, indent=1), encoding="utf-8")
    os.chmod(tmp, 0o600)
    tmp.replace(path)
    with _lock:
        _cache = None


def target_root(name: str, *, require: bool = True) -> Path:
    """保存先のルート。require=True なら目印が一致しない（外れている）ときに StorageUnavailable。"""
    cfg = load()
    target = cfg["targets"].get(name)
    if target is None:
        raise StorageError(f"保存先がありません: {name}")
    root = Path(target["path"])
    if name == LOCAL:
        root.mkdir(parents=True, exist_ok=True)
        if _read_marker(root) is None:
            init_target(LOCAL)
        return root
    if require:
        marker = _read_marker(root)
        if marker is None or not target.get("id") or marker.get("id") != target["id"]:
            raise StorageUnavailable(f"保存先「{name}」を使えません（目印が無い・一致しない。マウントを確認）")
    return root


def available(name: str) -> bool:
    try:
        target_root(name)
        return True
    except StorageError:
        return False


def status(name: str) -> dict:
    cfg = load()
    target = cfg["targets"][name]
    root = Path(target["path"])
    marker = _read_marker(root) if root.exists() else None
    ok = available(name)
    info = filesystem(root)
    out = {"name": name, "label": target.get("label", ""), "path": str(root), "exists": root.exists(),
           "initialized": marker is not None, "available": ok, **info,
           "writable": bool(root.exists() and os.access(root, os.W_OK))}
    if root.exists():
        usage = shutil.disk_usage(root)
        out |= {"free_bytes": usage.free, "total_bytes": usage.total}
    return out


# ---- 置き場所の解決 ----

def db_target(shard: str) -> str:
    if shard not in SHARDS:
        raise StorageError(f"不正な分野: {shard}")
    return load()["placement"]["db"][shard]


def db_path(shard: str) -> Path:
    return target_root(db_target(shard)) / f"{shard}.sqlite3"


def graphs_dir() -> Path:
    path = target_root(load()["placement"]["graphs"]) / "graphs"
    path.mkdir(parents=True, exist_ok=True)
    return path


def category_for_role(role: str) -> str:
    return ROLE_CATEGORY.get(role, "originals")


def blob_target_for(role: str) -> str:
    return load()["placement"][category_for_role(role)]


def blob_dir(name: str, *, require: bool = True) -> Path:
    return target_root(name, require=require) / "blobs"


def backup_targets() -> list[str]:
    return list(load()["backup"]["targets"])
