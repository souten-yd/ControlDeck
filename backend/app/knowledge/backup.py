"""ナレッジのバックアップ・照合・別の場所への復元、保存先の移し替え。

バックアップ先（storage の backup.targets）ごとに:
  <保存先>/backups/<YYYYmmdd-HHMMSS>/
      {shard}.sqlite3       SQLite Online Backup で取った整合したスナップショット
      graphs/*.sqlite3      GraphRAG
      blobs-index.txt       この世代が参照する blobs の sha256 一覧
      manifest.json         各ファイルの SHA-256・大きさ・件数・スキーマ版・作成日時
  <保存先>/blobs/ab/<sha256>  原本・図・抽出結果の写し（内容のハッシュ名なので世代間で共有し、差分だけ送る）

稼働中の DB をネットワーク越しに開かない。スナップショットは「この PC」で作ってから保存先へ写す。
復元は稼働中のデータを上書きせず、指定した空のディレクトリへ戻して照合する（restore_to）。
"""
from __future__ import annotations

import hashlib
import json
import re
import shutil
import sqlite3
import subprocess
import tempfile
import time
from pathlib import Path

from app.knowledge import storage, store

SNAPSHOT_RE = re.compile(r"^\d{8}-\d{6}$")
MANIFEST_VERSION = 1


class BackupError(ValueError):
    pass


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def _snapshot_sqlite(src: Path, dst: Path) -> None:
    source = sqlite3.connect(f"file:{src}?mode=ro", uri=True, timeout=30)
    target = sqlite3.connect(dst)
    try:
        source.backup(target)
        ok = target.execute("PRAGMA quick_check").fetchone()[0]
    finally:
        target.close()
        source.close()
    if ok != "ok":
        raise BackupError(f"スナップショットの整合性検査に失敗: {src.name}: {ok}")


def _counts(path: Path) -> dict:
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        return {t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
                for t in ("collections", "documents", "chunks", "vectors", "blobs")}
    except sqlite3.Error:
        return {}
    finally:
        conn.close()


def _referenced_blobs() -> list[tuple[str, str]]:
    """(sha256, location) の一覧。"""
    out = []
    for shard in storage.SHARDS:
        path = store.shard_path(shard)
        if not path.exists():
            continue
        conn = store.connect(shard)
        try:
            out += [(r[0], r[1]) for r in conn.execute("SELECT sha256, location FROM blobs")]
        finally:
            conn.close()
    return out


def _copy_verified(src: Path, dst: Path, sha256: str) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    tmp = dst.with_name(dst.name + ".part")
    shutil.copyfile(src, tmp)
    if _sha256(tmp) != sha256:
        tmp.unlink(missing_ok=True)
        raise BackupError(f"写したファイルのハッシュが一致しません: {sha256}")
    tmp.replace(dst)


def run_backup(target: str | None = None, *, progress=None) -> list[dict]:
    """バックアップ先ごとに 1 世代を作る。使えない保存先は飛ばして結果に書く。"""
    cfg = storage.load()
    names = [target] if target else cfg["backup"]["targets"]
    if not names:
        raise BackupError("バックアップ先が設定されていません")
    include = set(cfg["backup"]["include"])
    storage.target_root(storage.LOCAL)  # 作業用の一時ディレクトリを置く
    results = []
    for name in names:
        try:
            root = storage.target_root(name)
        except storage.StorageError as exc:
            results.append({"target": name, "ok": False, "error": str(exc)})
            continue
        stamp = time.strftime("%Y%m%d-%H%M%S")
        final = root / "backups" / stamp
        if final.exists():
            time.sleep(1)
            stamp = time.strftime("%Y%m%d-%H%M%S")
            final = root / "backups" / stamp
        with tempfile.TemporaryDirectory(prefix="knowledge-snap-", dir=storage.local_root()) as tmpdir:
            work = Path(tmpdir)
            manifest: dict = {"version": MANIFEST_VERSION, "created": time.time(), "stamp": stamp,
                              "schema": "controldeck.knowledge/1", "files": {}, "shards": {}, "blobs": {}}
            if "db" in include:
                for shard in storage.SHARDS:
                    src = store.shard_path(shard)
                    if src.exists():
                        if progress:
                            progress(f"{name}: {shard} のスナップショット")
                        _snapshot_sqlite(src, work / f"{shard}.sqlite3")
                        manifest["shards"][shard] = _counts(work / f"{shard}.sqlite3")
            if "graphs" in include:
                gdir = storage.graphs_dir()
                for graph in sorted(gdir.glob("*.sqlite3")):
                    (work / "graphs").mkdir(exist_ok=True)
                    _snapshot_sqlite(graph, work / "graphs" / graph.name)
            blob_categories = {c for c in storage.BLOB_CATEGORIES if c in include}
            index, copied, copied_bytes, missing = [], 0, 0, []
            if blob_categories:
                dest_blobs = storage.blob_dir(name)
                for sha, location in _referenced_blobs():
                    info = store.blob_info(sha)
                    role = info[1]["role"] if info else "original"
                    if storage.category_for_role(role) not in blob_categories:
                        continue
                    index.append(sha)
                    dst = dest_blobs / sha[:2] / sha
                    if dst.exists():
                        continue
                    try:
                        src = store.blob_path(sha)
                    except store.KnowledgeError:
                        missing.append(sha)
                        continue
                    _copy_verified(src, dst, sha)
                    copied += 1
                    copied_bytes += dst.stat().st_size
                (work / "blobs-index.txt").write_text("\n".join(sorted(set(index))) + ("\n" if index else ""))
            manifest["blobs"] = {"count": len(set(index)), "copied": copied, "copied_bytes": copied_bytes,
                                 "missing": missing}
            for path in sorted(work.rglob("*")):
                if path.is_file():
                    manifest["files"][str(path.relative_to(work))] = {"sha256": _sha256(path),
                                                                       "bytes": path.stat().st_size}
            (work / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=1))
            staging = root / "backups" / f".{stamp}.part"
            if staging.exists():
                shutil.rmtree(staging)
            shutil.copytree(work, staging)
            _sync_dir(staging)
            staging.rename(final)
        pruned = prune(name)
        verified = verify(name, stamp)
        results.append({"target": name, "ok": verified["ok"] and not missing, "stamp": stamp, "path": str(final),
                        "blobs": manifest["blobs"], "shards": manifest["shards"], "pruned": pruned,
                        "verify": verified})
    return results


def _sync_dir(path: Path) -> None:
    # NAS へ写したファイルを確実に書き出す（coreutils の sync。配列引数、shell なし）
    try:
        subprocess.run(["sync", "-f", str(path)], check=False, timeout=120)
    except (OSError, subprocess.SubprocessError):
        pass


def snapshots(name: str) -> list[dict]:
    root = storage.target_root(name)
    base = root / "backups"
    out = []
    for path in sorted(base.iterdir(), reverse=True) if base.exists() else []:
        if not (path.is_dir() and SNAPSHOT_RE.match(path.name)):
            continue
        try:
            manifest = json.loads((path / "manifest.json").read_text())
        except (OSError, json.JSONDecodeError):
            out.append({"stamp": path.name, "valid": False})
            continue
        total = sum(f["bytes"] for f in manifest.get("files", {}).values())
        out.append({"stamp": path.name, "valid": True, "created": manifest.get("created"),
                    "shards": manifest.get("shards", {}), "blobs": manifest.get("blobs", {}).get("count", 0),
                    "bytes": total})
    return out


def prune(name: str) -> list[str]:
    """設定の世代数を超えた古い世代を消す（このアプリが作った名前の世代だけ）。blobs は残す。"""
    keep = storage.load()["backup"]["keep"]
    valid = [s["stamp"] for s in snapshots(name) if s.get("valid")]
    removed = []
    base = storage.target_root(name) / "backups"
    for stamp in valid[keep:]:
        path = base / stamp
        if SNAPSHOT_RE.match(stamp) and path.parent == base:
            shutil.rmtree(path)
            removed.append(stamp)
    return removed


def _snapshot_dir(name: str, stamp: str) -> Path:
    if not SNAPSHOT_RE.match(stamp or ""):
        raise BackupError("不正な世代名")
    path = storage.target_root(name) / "backups" / stamp
    if not path.is_dir():
        raise BackupError(f"世代がありません: {stamp}")
    return path


def verify(name: str, stamp: str) -> dict:
    """世代のファイルのハッシュ・SQLite の整合性・参照する blobs の存在を確かめる。"""
    path = _snapshot_dir(name, stamp)
    manifest = json.loads((path / "manifest.json").read_text())
    problems = []
    for rel, meta in manifest.get("files", {}).items():
        file = path / rel
        if not file.exists():
            problems.append(f"無い: {rel}")
        elif _sha256(file) != meta["sha256"]:
            problems.append(f"ハッシュ不一致: {rel}")
        elif rel.endswith(".sqlite3"):
            conn = sqlite3.connect(f"file:{file}?mode=ro", uri=True)
            try:
                if conn.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                    problems.append(f"整合性検査に失敗: {rel}")
            finally:
                conn.close()
    blobs = storage.blob_dir(name)
    index = path / "blobs-index.txt"
    count = 0
    if index.exists():
        for sha in index.read_text().split():
            count += 1
            if not (blobs / sha[:2] / sha).exists():
                problems.append(f"blob が無い: {sha}")
    return {"ok": not problems, "problems": problems[:50], "blobs_checked": count}


def restore_to(name: str, stamp: str, dest: Path) -> dict:
    """世代を空のディレクトリへ戻す（稼働中のデータは上書きしない）。戻した DB の件数を返す。"""
    dest = Path(dest).expanduser().resolve()
    if dest.exists() and any(dest.iterdir()):
        raise BackupError("復元先は空のディレクトリにしてください")
    checked = verify(name, stamp)
    if not checked["ok"]:
        raise BackupError("世代の照合に失敗したので復元しません: " + "; ".join(checked["problems"][:5]))
    src = _snapshot_dir(name, stamp)
    dest.mkdir(parents=True, exist_ok=True)
    for item in src.iterdir():
        if item.name == "manifest.json" or item.suffix == ".sqlite3" or item.name in ("graphs", "blobs-index.txt"):
            (shutil.copytree if item.is_dir() else shutil.copy2)(item, dest / item.name)
    blobs_src = storage.blob_dir(name)
    index = src / "blobs-index.txt"
    for sha in (index.read_text().split() if index.exists() else []):
        _copy_verified(blobs_src / sha[:2] / sha, dest / "blobs" / sha[:2] / sha, sha)
    return {"path": str(dest), "shards": {p.stem: _counts(p) for p in dest.glob("*.sqlite3")}}


def relocate_blobs(*, dry_run: bool = True, keep_source: bool = False, progress=None) -> dict:
    """blobs を設定どおりの保存先へ移す（NAS が外れていた間に「この PC」へ置いた分など）。"""
    moves, moved, failed = [], 0, []
    for shard in storage.SHARDS:
        if not store.shard_path(shard).exists():
            continue
        conn = store.connect(shard)
        try:
            rows = conn.execute("SELECT sha256, location, role FROM blobs").fetchall()
        finally:
            conn.close()
        for sha, location, role in rows:
            wanted = storage.blob_target_for(role)
            if wanted != location:
                moves.append((shard, sha, location, wanted))
    if dry_run:
        return {"dry_run": True, "planned": [{"sha256": s, "from": a, "to": b} for _, s, a, b in moves]}
    for shard, sha, location, wanted in moves:
        try:
            src = storage.blob_dir(location) / sha[:2] / sha
            dst = storage.blob_dir(wanted) / sha[:2] / sha
            if not dst.exists():
                _copy_verified(src, dst, sha)
            conn = store.connect(shard)
            try:
                conn.execute("UPDATE blobs SET location=? WHERE sha256=?", (wanted, sha))
                conn.commit()
            finally:
                conn.close()
            if not keep_source and not _still_used(sha, location):
                src.unlink(missing_ok=True)
            moved += 1
            if progress:
                progress(f"{moved}/{len(moves)}")
        except (OSError, storage.StorageError, BackupError) as exc:
            failed.append({"sha256": sha, "error": str(exc)})
    return {"dry_run": False, "moved": moved, "failed": failed}


def _still_used(sha: str, location: str) -> bool:
    for shard in storage.SHARDS:
        if not store.shard_path(shard).exists():
            continue
        conn = store.connect(shard)
        try:
            if conn.execute("SELECT 1 FROM blobs WHERE sha256=? AND location=?", (sha, location)).fetchone():
                return True
        finally:
            conn.close()
    return False


def move_db(shard: str, target: str) -> dict:
    """分野の DB を別の保存先へ移す。ControlDeck を止めた状態で CLI から実行する。

    整合したスナップショットを新しい場所に作り、照合してから設定を切り替える。古いファイルは
    <名前>.moved-<日時> に改名して残す（消さない）。
    """
    if shard not in storage.SHARDS:
        raise BackupError(f"不正な分野: {shard}")
    cfg = storage.load()
    current = cfg["placement"]["db"][shard]
    if current == target:
        return {"shard": shard, "unchanged": True}
    cfg["placement"]["db"][shard] = target
    storage.validate(cfg)  # ネットワーク FS などをここで拒否
    src = storage.target_root(current) / f"{shard}.sqlite3"
    dst_root = storage.target_root(target)
    dst = dst_root / f"{shard}.sqlite3"
    if dst.exists():
        raise BackupError(f"移し先に既に DB があります: {dst}")
    if src.exists():
        _snapshot_sqlite(src, dst)
        if _counts(dst) != _counts(src):
            dst.unlink()
            raise BackupError("移した DB の件数が一致しません")
    storage._write_raw(storage.validate(cfg))
    if src.exists():
        stamp = time.strftime("%Y%m%d-%H%M%S")
        for suffix in ("", "-wal", "-shm"):
            old = Path(str(src) + suffix)
            if old.exists():
                old.rename(old.with_name(old.name + f".moved-{stamp}"))
    return {"shard": shard, "from": current, "to": target, "path": str(dst)}
