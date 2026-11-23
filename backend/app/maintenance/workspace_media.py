"""作業場の画面の記録（撮影・操作テストの動画）。一覧・配信・削除（ROADMAP U28・U34）。

置き場は作業場の repo の中: `.controldeck/media/`（箱の ui-verifier の Playwright MCP の出力先）と
`test-results/`（E2E の撮影）。箱の AI が書ける場所なので、ホストでは symlink を一切辿らない。
- 各段を O_NOFOLLOW・dir_fd で開く（sandbox._box_file_tail と同じ考え方）。ふつうのファイルだけを扱う。
- パスは一覧に出た相対パスだけ（`..`・絶対パス・置き場の外・許す拡張子以外は断る）。
- 消すのもホストで dir_fd の unlink（symlink に当たってもリンク自体を消すだけで、先は消えない）。
"""
from __future__ import annotations

import os
import stat
from pathlib import Path

from app.maintenance import sandbox
from app.maintenance.sandbox import Sandbox, SandboxError

MEDIA_ROOTS = (".controldeck/media", "test-results")
# 箱の AI が書けるので、中身を決めるのは拡張子（許した content-type だけ返す）
CONTENT_TYPES = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp",
                 ".webm": "video/webm", ".mp4": "video/mp4"}
MAX_FILE_BYTES = 300 * 1024 * 1024
LIST_LIMIT = 200          # 一覧に返す件数の上限（新しい順）
SCAN_LIMIT = 5000         # 数え上げるファイルの上限（件数と合計はここまでの値）
MAX_DEPTH = 6
DIR_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW


class MediaNotFound(SandboxError):
    """記録が無い（消された・置き場の外・ふつうのファイルでない）。"""


def kind_of(name: str) -> str:
    return "video" if Path(name).suffix.lower() in (".webm", ".mp4") else "image"


def content_type(path: str) -> str:
    return CONTENT_TYPES.get(Path(path).suffix.lower(), "")


def _repo_fd(box: Sandbox) -> int:
    """box.repo を、sandbox の root から 1 段ずつ O_NOFOLLOW で開く（途中を symlink に差し替えられない）。"""
    fd = os.open(sandbox.root(), DIR_FLAGS)
    try:
        for part in (box.name, "work", "repo"):
            nxt = os.open(part, DIR_FLAGS, dir_fd=fd)
            os.close(fd)
            fd = nxt
    except OSError:
        os.close(fd)
        raise MediaNotFound("作業場の場所を開けません") from None
    return fd


def _split(rel: str) -> list[str]:
    """一覧に出る形の相対パスだけ通す。"""
    if not rel or "\x00" in rel or rel.startswith("/") or "\\" in rel:
        raise MediaNotFound("パスが正しくありません")
    parts = rel.split("/")
    if any(p in ("", ".", "..") for p in parts):
        raise MediaNotFound("パスが正しくありません")
    if not any(rel.startswith(root + "/") for root in MEDIA_ROOTS) or not content_type(rel):
        raise MediaNotFound("画面の記録ではありません")
    return parts


def _open_parent(box: Sandbox, parts: list[str]) -> int:
    fd = _repo_fd(box)
    try:
        for part in parts[:-1]:
            nxt = os.open(part, DIR_FLAGS, dir_fd=fd)
            os.close(fd)
            fd = nxt
    except OSError:
        os.close(fd)
        raise MediaNotFound("記録がありません") from None
    return fd


def _regular(name: str, parent_fd: int) -> os.stat_result:
    try:
        info = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
    except OSError:
        raise MediaNotFound("記録がありません") from None
    if not stat.S_ISREG(info.st_mode):
        raise MediaNotFound("記録がありません")
    return info


def open_file(box: Sandbox, rel: str) -> tuple[int, int, str]:
    """(fd, 大きさ, content-type)。呼び側が fd を閉じる。ふつうのファイルで上限以下のものだけ。"""
    parts = _split(rel)
    parent = _open_parent(box, parts)
    try:
        _regular(parts[-1], parent)
        try:
            fd = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
        except OSError:
            raise MediaNotFound("記録がありません") from None
    finally:
        os.close(parent)
    info = os.fstat(fd)
    if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_FILE_BYTES:
        os.close(fd)
        raise MediaNotFound("記録を開けません（大きすぎるか、ふつうのファイルではありません）")
    return fd, info.st_size, content_type(rel)


def _scan(fd: int, prefix: str, depth: int, found: list[dict]) -> None:
    """fd のフォルダを辿ってふつうのファイルを found へ。symlink は入れも辿りもしない。"""
    with os.scandir(fd) as entries:
        for entry in entries:
            if len(found) >= SCAN_LIMIT:
                return
            rel = f"{prefix}/{entry.name}"
            try:
                if entry.is_dir(follow_symlinks=False):
                    if depth < MAX_DEPTH:
                        sub = os.open(entry.name, DIR_FLAGS, dir_fd=fd)
                        try:
                            _scan(sub, rel, depth + 1, found)
                        finally:
                            os.close(sub)
                elif entry.is_file(follow_symlinks=False) and content_type(entry.name):
                    info = entry.stat(follow_symlinks=False)
                    found.append({"path": rel, "kind": kind_of(entry.name), "size": info.st_size, "mtime": info.st_mtime})
            except OSError:
                continue


def _all(box: Sandbox) -> list[dict]:
    found: list[dict] = []
    try:
        repo = _repo_fd(box)
    except MediaNotFound:
        return found
    try:
        for root in MEDIA_ROOTS:
            fd = repo
            opened: list[int] = []
            try:
                for part in root.split("/"):
                    fd = os.open(part, DIR_FLAGS, dir_fd=fd)
                    opened.append(fd)
            except OSError:
                for item in opened:
                    os.close(item)
                continue
            try:
                _scan(fd, root, 0, found)
            finally:
                for item in opened:
                    os.close(item)
    finally:
        os.close(repo)
    return found


def media_list(box: Sandbox, since: float | None = None) -> dict:
    """{items(新しい順・LIST_LIMIT 件まで), count, total_size}。since があればそれより新しいものだけ。"""
    found = sorted(_all(box), key=lambda item: item["mtime"], reverse=True)
    if since is not None:
        found = [item for item in found if item["mtime"] >= since]
    return {"items": found[:LIST_LIMIT], "count": len(found), "total_size": sum(i["size"] for i in found)}


def delete_one(box: Sandbox, rel: str) -> int:
    """1 件を消して、消した大きさを返す。無ければ MediaNotFound。"""
    parts = _split(rel)
    parent = _open_parent(box, parts)
    try:
        size = _regular(parts[-1], parent).st_size
        try:
            os.unlink(parts[-1], dir_fd=parent)
        except OSError:
            raise MediaNotFound("記録がありません") from None
        return size
    finally:
        os.close(parent)


def delete_all(box: Sandbox) -> dict:
    """この作業場の記録を全部消す。{count, size}。"""
    count = size = 0
    for item in _all(box):
        try:
            size += delete_one(box, item["path"])
            count += 1
        except MediaNotFound:
            continue
    return {"count": count, "size": size}


def summary_for(username: str) -> dict:
    """自分の全作業場の記録の合計と、作業場ごとの件数・大きさ。"""
    from app.maintenance import workspaces

    rows = []
    for box in workspaces._boxes_of(username):
        listed = media_list(box)
        rows.append({"name": box.name, "count": listed["count"], "total_size": listed["total_size"]})
    return {"count": sum(r["count"] for r in rows), "total_size": sum(r["total_size"] for r in rows),
            "workspaces": [r for r in rows if r["count"]]}


def delete_all_for(username: str) -> dict:
    from app.maintenance import workspaces

    count = size = 0
    for box in workspaces._boxes_of(username):
        done = delete_all(box)
        count += done["count"]
        size += done["size"]
    return {"count": count, "size": size}
