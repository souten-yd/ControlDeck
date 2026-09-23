"""Observe surviving headless units after Host restart; never stop on observation."""
from __future__ import annotations

import asyncio
from contextlib import suppress
import re
import shutil
from typing import Any


class RecoveryError(RuntimeError):
    pass


def _unit(item: dict[str, Any]) -> str | None:
    job_id = item.get("id")
    if (item.get("persisted") is not True or item.get("kind") != "opencode.run"
            or item.get("status") != "interrupted" or not isinstance(job_id, str)
            or re.fullmatch(r"[A-Za-z0-9_-]{1,24}", job_id) is None):
        return None
    return f"cdfeature-opencode-{job_id}.service"


async def _command(arguments: list[str], *, timeout: float = 5) -> tuple[int, str]:
    executable = await asyncio.to_thread(shutil.which, "systemctl")
    if executable is None:
        raise RecoveryError("OpenCodeの実行状態を確認できません")
    process = None
    try:
        process = await asyncio.create_subprocess_exec(
            executable, "--user", *arguments,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
        )
        output, _ = await asyncio.wait_for(process.communicate(), timeout)
        if len(output) > 256 * 1024:
            raise RecoveryError("OpenCodeの実行状態を確認できません")
        return process.returncode, output.decode("utf-8", errors="replace")
    except (OSError, TimeoutError):
        raise RecoveryError("OpenCodeの実行状態を確認できません") from None
    finally:
        # Reap only our systemctl client on timeout/cancel, not any agent unit.
        if process is not None and process.returncode is None:
            with suppress(ProcessLookupError):
                process.kill()
            await process.wait()


async def _states(units: list[str]) -> dict[str, str]:
    states = {unit: "unknown" for unit in units}
    if not units:
        return states
    try:
        _, output = await _command([
            "show", "--property=Id,LoadState,ActiveState,SubState,MainPID", "--", *units,
        ])
    except RecoveryError:
        return states
    for block in output.strip().split("\n\n"):
        fields = dict(line.split("=", 1) for line in block.splitlines() if "=" in line)
        unit = fields.get("Id")
        if unit not in states:
            continue
        if fields.get("LoadState") == "not-found":
            states[unit] = "stopped"
            continue
        if fields.get("LoadState") != "loaded":
            continue
        active = fields.get("ActiveState")
        pid = fields.get("MainPID", "")
        if active in {"inactive", "failed"} or (active == "active" and fields.get("SubState") == "exited"):
            states[unit] = "stopped"
        elif active in {"active", "activating", "deactivating"} and pid.isdigit() and int(pid) > 0:
            states[unit] = "stopping" if active == "deactivating" else "running"
    return states


async def reconcile(items: list[dict[str, Any]]) -> None:
    candidates = [(item, unit) for item in items if (unit := _unit(item)) is not None]
    states = await _states([unit for _, unit in candidates])
    for item, unit in candidates:
        state = states[unit]
        if state in {"running", "stopping"}:
            item.update(status="running", phase="external_running", error="", finished_at=None)
            label = "OpenCodeを停止中" if state == "stopping" else "Host再起動後もOpenCodeは実行中"
        elif state == "unknown":
            item["phase"] = "external_status_unknown"
            item["error"] = "OpenCodeの現在の実行状態を確認できません。停止済みとは判断していません。"
            label = "OpenCodeの実行状態を確認できません"
        else:
            item["phase"] = "external_result_unavailable"
            item["error"] = "OpenCodeは実行中ではありません。Host再起動で失われた結果は確認できません。"
            label = "OpenCodeの結果を確認できません"
        item["progress"] = {**(item.get("progress") or {}), "status": label}


def _record_cancel(job_id: str, owner_user_id: int | None) -> bool:
    from app.database import SessionLocal
    from app.models import Job, JobControl, utcnow

    with SessionLocal() as db:
        row = db.get(Job, job_id)
        if (row is None or row.kind != "opencode.run" or row.status != "interrupted"
                or row.owner_user_id != owner_user_id):
            return False
        row.status = "canceled"
        row.phase = None
        row.error = "利用者が継続中のOpenCode実行を停止しました"
        row.finished_at = utcnow()
        control = db.get(JobControl, job_id)
        if control is not None:
            control.revision += 1
            control.heartbeat_at = utcnow()
        db.commit()
    return True


async def cancel_surviving(item: dict[str, Any]) -> bool:
    """Called only after the Jobs endpoint has checked the user's authority."""
    unit = _unit(item)
    if unit is None:
        return False
    state = (await _states([unit]))[unit]
    if state == "unknown":
        raise RecoveryError("OpenCodeの実行状態を確認できないため、停止完了とは扱いません")
    if state == "stopped":
        return False
    code, _ = await _command(["stop", "--", unit], timeout=15)
    if code != 0 or (await _states([unit]))[unit] != "stopped":
        raise RecoveryError("OpenCodeの停止を確認できません。状態を確認してから再操作してください")
    return await asyncio.to_thread(_record_cancel, item["id"], item.get("owner_user_id"))
