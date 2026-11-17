"""OpenCode の TUI セッションの記録（OpenCode 画面の一覧と「続きから開く」に使う）。

TUI は --standalone で動き、閉じると tmux のセッションごと終わる。終わったあとも
「どのプロジェクトで開いていたか」を残して、--continue で続きから開けるようにする。
"""
from __future__ import annotations

import json
import threading
import time
from pathlib import Path

from app.config import data_dir

MAX_ENTRIES = 40  # 利用者ごと
# 単一プロセス（uvicorn の workers 指定なし）が前提。複数 worker にするならファイルロックへ替える。
_lock = threading.Lock()


def _path() -> Path:
    return data_dir() / "integrations" / "opencode" / "tui-sessions.json"


def _load() -> list[dict]:
    try:
        raw = json.loads(_path().read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    return [item for item in raw if isinstance(item, dict) and isinstance(item.get("terminal_id"), str)] \
        if isinstance(raw, list) else []


def _save(items: list[dict]) -> None:
    path = _path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    kept: list[dict] = []
    per_user: dict = {}
    for item in reversed(items):  # 新しいものから、利用者ごとに MAX_ENTRIES 件
        owner = item.get("owner_user_id")
        per_user[owner] = per_user.get(owner, 0) + 1
        if per_user[owner] <= MAX_ENTRIES:
            kept.append(item)
    tmp.write_text(json.dumps(list(reversed(kept)), ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)


def record(terminal_id: str, project_path: str, owner_user_id: int | None, *, resume: str = "") -> None:
    with _lock:
        items = [item for item in _load() if item.get("terminal_id") != terminal_id]
        items.append({"terminal_id": terminal_id, "project_path": str(project_path),
                      "project_name": Path(str(project_path)).name, "owner_user_id": owner_user_id,
                      "started_at": time.time(), "resumed": bool(resume)})
        _save(items)


def forget_project(project_path: str, owner_user_id: int | None, alive: set[str] | frozenset = frozenset()) -> None:
    """閉じた記録だけを外す（別の端末で開いたばかりの動いているものは残す）。"""
    with _lock:
        _save([item for item in _load()
               if not (item.get("project_path") == project_path and item.get("owner_user_id") == owner_user_id
                       and item.get("terminal_id") not in alive)])


def listing(owner_user_id: int | None, alive: dict[str, dict]) -> list[dict]:
    """新しい順。閉じたセッションはプロジェクトごとに最新の 1 件だけ（続きから開く入口）。

    動いているセッションは全部出す。動いているプロジェクトの閉じたものは出さない。
    """
    items = [item for item in _load() if item.get("owner_user_id") == owner_user_id]
    items.sort(key=lambda item: float(item.get("started_at") or 0), reverse=True)
    running_projects = {item["project_path"] for item in items if item["terminal_id"] in alive}
    out: list[dict] = []
    closed_seen: set[str] = set()
    for item in items:
        project = str(item.get("project_path") or "")
        running = item["terminal_id"] in alive
        if not running:
            if project in running_projects or project in closed_seen or not Path(project).is_dir():
                continue
            closed_seen.add(project)
        session = alive.get(item["terminal_id"]) or {}
        out.append({
            "terminal_id": item["terminal_id"], "project_path": project,
            "project_name": item.get("project_name") or Path(project).name,
            "started_at": item.get("started_at"), "running": running,
            "attached": bool(session.get("attached")), "persistent": bool(session.get("persistent")),
        })
    return out
