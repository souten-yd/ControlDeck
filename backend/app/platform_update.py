"""Release-based ControlDeck updater: authenticated, opt-in and fail-closed.

The web process never changes its own code. A separate systemd --user transient
unit performs the update, logs persistent results, and restarts the web service.
No request accepts a source URL, refspec, branch, command or filesystem path.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import uuid
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict

from app.audit import service as audit
from app.config import REPO_ROOT, data_dir
from app.database import get_db
from app.models import User
from app.security.deps import require_permission

router = APIRouter(prefix="/system/updates", tags=["system-updates"])
RELEASE_URL = "https://api.github.com/repos/souten-yd/ControlDeck/releases/latest"
ALLOWED_ORIGINS = frozenset((
    "https://github.com/souten-yd/ControlDeck.git",
    "https://github.com/souten-yd/ControlDeck",
    "git@github.com:souten-yd/ControlDeck.git",
))
TAG_PATTERN = re.compile(r"^ControlDeck_v[0-9]+(?:\.[0-9]+){1,2}$")


def status_file() -> Path:
    return (data_dir() / "maintenance" / "control-deck-update.json").resolve()


def write_state(state: dict) -> None:
    path = status_file()
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    temp = path.with_name(path.name + ".tmp")
    temp.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
    os.chmod(temp, 0o600)
    os.replace(temp, path)


def read_state() -> dict:
    try:
        state = json.loads(status_file().read_text(encoding="utf-8"))
        return state if isinstance(state, dict) else {}
    except (OSError, ValueError):
        return {}


def git(*args: str, timeout: int = 25) -> str:
    try:
        result = subprocess.run(
            ["git", "-C", str(REPO_ROOT), *args],
            capture_output=True, text=True, timeout=timeout, check=False,
            env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RuntimeError("gitコマンドの実行環境を確認できません") from exc
    if result.returncode != 0:
        raise RuntimeError("git状態の確認に失敗しました")
    return result.stdout.strip()


def local_state() -> dict:
    result = {"head": "", "branch": "", "clean": False, "trusted_origin": False, "ready": False}
    try:
        toplevel = Path(git("rev-parse", "--show-toplevel")).resolve()
        if toplevel != Path(REPO_ROOT).resolve():
            return {**result, "reason": "管理対象外のGit checkoutです"}
        result["head"] = git("rev-parse", "HEAD")
        result["branch"] = git("branch", "--show-current")
        result["clean"] = not bool(git("status", "--porcelain", "--untracked-files=normal"))
        result["trusted_origin"] = git("remote", "get-url", "origin") in ALLOWED_ORIGINS
        result["ready"] = (result["branch"] == "main" and result["clean"] and result["trusted_origin"])
        if not result["ready"]:
            result["reason"] = "main以外、作業ツリーに変更あり、または配布元originが一致しません"
    except RuntimeError as exc:
        result["reason"] = str(exc)
    return result


def latest_release() -> dict:
    req = urllib.request.Request(RELEASE_URL, headers={
        "User-Agent": "ControlDeck-release-updater",
        "Accept": "application/vnd.github+json",
    })
    try:
        with urllib.request.urlopen(req, timeout=20) as response:
            info = json.loads(response.read(256_001))
    except (OSError, ValueError) as exc:
        raise RuntimeError("GitHub Releaseを確認できません") from exc
    tag = str(info.get("tag_name") or "")
    if not TAG_PATTERN.fullmatch(tag) or info.get("draft") or info.get("prerelease"):
        raise RuntimeError("正式Releaseの形式を確認できません")
    if info.get("html_url") != f"https://github.com/souten-yd/ControlDeck/releases/tag/{tag}":
        raise RuntimeError("Release URLが期待した配布元と異なります")
    return {"tag": tag, "url": info["html_url"], "published_at": info.get("published_at", "")}


@router.get("")
async def get_update_status(user: User = Depends(require_permission("settings.manage"))):
    from asyncio import to_thread

    local, state = await to_thread(local_state), read_state()
    try:
        release = await to_thread(latest_release)
        error = ""
    except RuntimeError as exc:
        release = None
        error = str(exc)
    try:
        tags = await to_thread(git, "tag", "--points-at", "HEAD")
        installed_tag = next((tag for tag in tags.splitlines() if TAG_PATTERN.fullmatch(tag)), "")
    except RuntimeError:
        installed_tag = ""
    return {
        "local": local,
        "installed_tag": installed_tag,
        "latest": release,
        "update_available": bool(release and local["ready"] and installed_tag != release["tag"]),
        "last_operation": state,
        "error": error,
    }


class UpdateBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tag: str


@router.post("/apply", status_code=202)
async def apply_update(
    body: UpdateBody, request: Request,
    user: User = Depends(require_permission("settings.manage")), db=Depends(get_db),
):
    from asyncio import to_thread

    local = await to_thread(local_state)
    if not local["ready"]:
        raise HTTPException(409, detail=local.get("reason") or "更新できないGit作業ツリーです")
    latest = await to_thread(latest_release)
    if body.tag != latest["tag"]:
        raise HTTPException(409, detail="Releaseが更新されています。確認してから再実行してください")
    old = read_state()
    if old.get("status") in ("queued", "running"):
        raise HTTPException(409, detail="既にControlDeck本体の更新処理が実行中です")
    if not shutil_which_systemd_run():
        raise HTTPException(409, detail="systemd-run --user を利用できません")
    task_id = uuid.uuid4().hex[:12]
    state = {
        "id": task_id, "status": "queued", "target_tag": body.tag,
        "started_at": datetime.now(timezone.utc).isoformat(),
        "previous_head": local["head"], "message": "更新ジョブを起動しています",
    }
    write_state(state)
    cmd = [
        "systemd-run", "--user", "--collect",
        "--unit", f"control-deck-update-{task_id}",
        "--working-directory", str(Path(REPO_ROOT) / "backend"),
        sys.executable, "-m", "app.platform_update_worker", body.tag, task_id,
    ]
    try:
        result = await to_thread(subprocess.run, cmd, capture_output=True, text=True, timeout=15, check=False)
    except (OSError, subprocess.TimeoutExpired):
        result = None
    if result is None or result.returncode != 0:
        write_state({**state, "status": "failed", "message": "systemd更新ジョブを起動できませんでした"})
        raise HTTPException(503, detail="ユーザーsystemd更新ジョブを起動できませんでした")
    audit.record(db, "system.update.apply", user=user, resource_type="system",
                 resource_id=body.tag, request=request,
                 metadata={"task_id": task_id, "previous_head": local["head"]})
    return {"id": task_id, "status": "queued"}


def shutil_which_systemd_run() -> bool:
    from shutil import which
    return which("systemd-run") is not None
