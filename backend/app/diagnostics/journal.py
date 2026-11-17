"""journal から再起動・異常終了・OOM を抜き出す（読むだけ）。

- ControlDeck 本体（control-deck-web.service）の起動・停止と、止まった理由
  （watchdog / oom-kill / exit-code / signal / 更新 / 手動）
- ControlDeck が管理する常駐（control-deck-* / cdapp-* / cd-*）の異常終了と OOM
- カーネルの OOM killer の記録（殺されたプロセス、使っていたメモリ、属していた unit）
"""
from __future__ import annotations

import json
import re
import subprocess
import time
from datetime import datetime

WEB_UNIT = "control-deck-web.service"
MANAGED = re.compile(r"^(control-deck-|cdapp-|cd-)[\w.@-]+\.(service|scope)$")
_FAILED = re.compile(r"^(?P<unit>[\w.@\\-]+): Failed with result '(?P<result>[\w-]+)'")
_EXITED = re.compile(r"^(?P<unit>[\w.@\\-]+): Main process exited, code=(?P<code>\w+), status=(?P<status>[^ ]+)")
_OOM_UNIT = re.compile(r"^(?P<unit>[\w.@\\-]+): A process of this unit has been killed by the OOM killer")
_WATCHDOG = re.compile(r"^(?P<unit>[\w.@\\-]+): Watchdog timeout")
_KILLED = re.compile(r"Out of memory: Killed process (?P<pid>\d+) \((?P<name>[^)]+)\) total-vm:(?P<vm>\d+)kB, "
                     r"anon-rss:(?P<anon>\d+)kB, file-rss:(?P<file>\d+)kB, shmem-rss:(?P<shmem>\d+)kB")
_MEMCG = re.compile(r"oom-kill:.*task_memcg=(?P<memcg>[^,]+)")


def _journal(args: list[str], since: float, timeout: int = 30) -> list[dict]:
    started = datetime.fromtimestamp(since).strftime("%Y-%m-%d %H:%M:%S")
    try:
        result = subprocess.run(["journalctl", *args, "--since", started, "-o", "json",
                                 "--output-fields=MESSAGE,USER_UNIT,UNIT", "--no-pager"],
                                capture_output=True, text=True, timeout=timeout, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return []
    rows = []
    for line in result.stdout.splitlines():
        try:
            row = json.loads(line)
        except ValueError:
            continue
        message = row.get("MESSAGE")
        if isinstance(message, list):  # バイト列で来ることがある
            message = bytes(message).decode("utf-8", "replace")
        rows.append({"ts": int(row.get("__REALTIME_TIMESTAMP", 0)) / 1e6, "message": str(message or ""),
                     "unit": row.get("USER_UNIT") or row.get("UNIT") or ""})
    return rows


def _unit_from_memcg(memcg: str) -> str:
    parts = [part for part in memcg.split("/") if part.endswith((".service", ".scope"))]
    return parts[-1].replace("\\x2d", "-") if parts else memcg


def oom_kills(since: float) -> list[dict]:
    rows = _journal(["-k", "-g", "Out of memory: Killed process|oom-kill:"], since)
    out: list[dict] = []
    memcg = ""
    for row in rows:
        found = _MEMCG.search(row["message"])
        if found:
            memcg = found.group("memcg")
            continue
        found = _KILLED.search(row["message"])
        if found:
            out.append({"ts": row["ts"], "pid": int(found.group("pid")), "process": found.group("name"),
                        "anon_rss_gb": round(int(found.group("anon")) / 2**20, 2),
                        "shmem_rss_gb": round(int(found.group("shmem")) / 2**20, 2),
                        "unit": _unit_from_memcg(memcg)})
            memcg = ""
    return out


def unit_events(since: float) -> dict:
    """systemd（利用者側）の記録から、本体の再起動と管理下の異常終了を抜き出す。"""
    rows = _journal(["--user", "SYSLOG_IDENTIFIER=systemd"], since)
    restarts: list[dict] = []
    failures: list[dict] = []
    pending: dict | None = None
    for row in rows:
        message, unit = row["message"], row["unit"]
        if unit == WEB_UNIT or message.startswith(WEB_UNIT):
            if message.startswith("Stopping ") or "Watchdog timeout" in message or "Failed with result" in message \
                    or "Main process exited" in message or "killed by the OOM killer" in message:
                pending = pending or {"ts": row["ts"], "reasons": []}
                pending["reasons"].append(message[:200])
            elif message.startswith("Started "):
                reasons = pending["reasons"] if pending else []
                restarts.append({"ts": row["ts"], "stopped_at": pending["ts"] if pending else None,
                                 "cause": _cause(reasons), "details": reasons[-4:]})
                pending = None
            continue
        for pattern, kind in ((_FAILED, "failed"), (_EXITED, "exited"), (_OOM_UNIT, "oom"), (_WATCHDOG, "watchdog")):
            found = pattern.match(message)
            if found and MANAGED.match(found.group("unit").replace("\\x2d", "-")):
                event = {"ts": row["ts"], "unit": found.group("unit"), "kind": kind, "message": message[:200]}
                if kind == "exited" and found.group("code") == "exited" and found.group("status") in ("0/SUCCESS",):
                    break
                failures.append(event)
                break
    # 更新による再起動（control-deck-update-* が直前に動いた）を見分ける
    updates = [row["ts"] for row in rows if row["message"].startswith("Started control-deck-update-")]
    for restart in restarts:
        stopped = restart.get("stopped_at") or restart["ts"]
        if restart["cause"] == "手動・通常の再起動" and any(0 <= stopped - ts <= 600 for ts in updates):
            restart["cause"] = "更新"
    return {"restarts": restarts, "failures": failures}


def _cause(reasons: list[str]) -> str:
    text = " ".join(reasons)
    if "Watchdog timeout" in text or "'watchdog'" in text:
        return "watchdog（応答なし）"
    if "OOM" in text or "oom-kill" in text:
        return "OOM"
    if "code=killed" in text or "code=dumped" in text:
        return "シグナルで終了"
    if "'exit-code'" in text or "status=1" in text:
        return "異常終了"
    return "手動・通常の再起動"


def summary(hours: float = 72.0) -> dict:
    since = time.time() - hours * 3600
    units = unit_events(since)
    return {"hours": hours, "restarts": units["restarts"], "unit_failures": units["failures"],
            "oom_kills": oom_kills(since)}
