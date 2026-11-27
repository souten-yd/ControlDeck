"""Nexarch の AI チーム（T1）: タスク板と、担当のコンソールへの送り込み。

チーム = Nexarch のコンソール（役割つき）の集まり + タスク板。状態の正本はこの板（モデルの記憶に
頼らない）。担当のコンソールへは Terminal の inject_input（自動再開と同じ送り方）で短い文を渡し、
終わった報告は画面の「完了報告:」の 5 行（古い 4 行も可）を正規表現で読む（LLM なし）。

- 保存: `data/ai-commander/teams.json`（0600・tmp → os.replace・プロセス内は RLock）。DB は使わない。
- 壊れた JSON は空として扱う（落とさない）。
- 自動の取り込み・止まった／上限の印・通知は team_watch.py（T2a）。ここは一覧のときの担当の状態・報告の有無・未検証の印。
根拠: docs/PLAN-nexarch-team.md T1。
"""
from __future__ import annotations

import json
import logging
import os
import re
import secrets
import threading
import time
from pathlib import Path

from app.commander import consoles, status
from app.config import data_dir
from app.terminals.manager import manager

logger = logging.getLogger(__name__)

ROLES = ("leader", "developer", "reviewer", "researcher", "member")
STATUSES = ("todo", "doing", "review", "done", "rejected")
NAME_MAX = 40
TITLE_MAX = 120
TEXT_MAX = 4000
FILES_MAX = 50
FILE_MAX = 500
HISTORY_MAX = 50
CAPTURE_LINES = 200
REPORT_FIELD_MAX = 2000

_lock = threading.RLock()


class TeamError(Exception):
    """router が HTTP の状態へ直す。status は 400/404/409。"""

    def __init__(self, message: str, status_code: int = 400) -> None:
        super().__init__(message)
        self.status_code = status_code


def _path() -> Path:
    return data_dir() / "ai-commander" / "teams.json"


def _load() -> list[dict]:
    try:
        raw = json.loads(_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    teams = raw.get("teams") if isinstance(raw, dict) else None
    return [t for t in teams if isinstance(t, dict) and isinstance(t.get("id"), str)] if isinstance(teams, list) else []


def _save(teams: list[dict]) -> None:
    path = _path()
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(".tmp")
    fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(json.dumps({"teams": teams}, ensure_ascii=False, indent=2))
    os.chmod(temp, 0o600)
    os.replace(temp, path)


# ---------------------------------------------------------------- 検証

def _has_control(value: str, *, multiline: bool) -> bool:
    allowed = {"\n", "\t"} if multiline else set()
    return any((ord(c) < 0x20 and c not in allowed) or ord(c) == 0x7F for c in value)


def clean_line(value: object, label: str, maximum: int, *, required: bool = True) -> str:
    """1 行の文字列（制御文字は断る）。"""
    if not isinstance(value, str):
        raise TeamError(f"{label}は文字列で指定してください")
    text = value.strip()
    if required and not text:
        raise TeamError(f"{label}を入力してください")
    if len(text) > maximum:
        raise TeamError(f"{label}は {maximum} 文字以内にしてください")
    if _has_control(text, multiline=False):
        raise TeamError(f"{label}に制御文字（改行・tab など）は使えません")
    return text


def clean_text(value: object, label: str, maximum: int = TEXT_MAX) -> str:
    """複数行の文字列（改行・tab 以外の制御文字は断る）。"""
    if not isinstance(value, str):
        raise TeamError(f"{label}は文字列で指定してください")
    text = value.strip()
    if len(text) > maximum:
        raise TeamError(f"{label}は {maximum} 文字以内にしてください")
    if _has_control(text, multiline=True):
        raise TeamError(f"{label}に制御文字は使えません（改行・tab は使えます）")
    return text


def clean_files(value: object) -> list[str]:
    if not isinstance(value, list):
        raise TeamError("担当ファイルは文字列のリストで指定してください")
    if len(value) > FILES_MAX:
        raise TeamError(f"担当ファイルは {FILES_MAX} 件までです")
    out: list[str] = []
    for item in value:
        text = clean_line(item, "担当ファイル", FILE_MAX, required=False)
        if text and text not in out:
            out.append(text)
    return out


def _now() -> float:
    return round(time.time(), 3)


def _event(task: dict, event: str, by: str) -> None:
    task.setdefault("history", []).append({"at": _now(), "event": event, "by": by})
    del task["history"][:-HISTORY_MAX]
    task["updated_at"] = _now()


def _find_team(teams: list[dict], team_id: str) -> dict:
    for team in teams:
        if team["id"] == team_id:
            return team
    raise TeamError("チームが見つかりません", 404)


def _find_task(team: dict, task_id: int) -> dict:
    for task in team.get("tasks", []):
        if task.get("id") == task_id:
            return task
    raise TeamError("タスクが見つかりません", 404)


# ---------------------------------------------------------------- 担当ファイルの重なり

def _overlaps(a: str, b: str) -> bool:
    a, b = a.strip().rstrip("/"), b.strip().rstrip("/")
    return a == b or a.startswith(b + "/") or b.startswith(a + "/")


def _warnings(team: dict, task: dict) -> list[str]:
    """同じチームの未完了（done 以外）の他のタスクと担当ファイルが重なるか。断らず知らせるだけ。"""
    if task.get("status") == "done":
        return []
    out: list[str] = []
    for other in team.get("tasks", []):
        if other is task or other.get("id") == task.get("id") or other.get("status") == "done":
            continue
        for mine in task.get("files", []):
            hit = next((theirs for theirs in other.get("files", []) if _overlaps(mine, theirs)), None)
            if hit is not None:
                out.append(f"#{other['id']} と {mine} が重なっています")
    return out


# ---------------------------------------------------------------- チーム

def _console_ids() -> dict[str, dict]:
    return {str(row["id"]): row for row in consoles.list_consoles()}


def create_team(name: str) -> dict:
    name = clean_line(name, "チーム名", NAME_MAX)
    with _lock:
        teams = _load()
        team = {
            "id": "t" + secrets.token_hex(4), "name": name, "created_at": _now(),
            "members": [], "tasks": [], "next_task_id": 1, "notify": True,
        }
        teams.append(team)
        _save(teams)
    return team


def rename_team(team_id: str, name: str) -> dict:
    name = clean_line(name, "チーム名", NAME_MAX)
    with _lock:
        teams = _load()
        team = _find_team(teams, team_id)
        team["name"] = name
        _save(teams)
    return team


def delete_team(team_id: str) -> dict:
    with _lock:
        teams = _load()
        team = _find_team(teams, team_id)
        teams.remove(team)
        _save(teams)
    # 役割の印は外す（他のチームのメンバーでなければ）
    still = {m["session_id"] for t in teams for m in t.get("members", [])}
    for member in team.get("members", []):
        if member["session_id"] not in still:
            _clear_role(member["session_id"])
    return team


def _clear_role(session_id: str) -> None:
    try:
        manager.clear_marks(session_id, ("role",))
    except (KeyError, OSError, RuntimeError, ValueError):
        pass  # 閉じたコンソールの印は外せなくてよい


def set_members(team_id: str, members: object) -> dict:
    """メンバーを丸ごと置き換える。Nexarch のコンソールだけ・リーダーは 0〜1 人。"""
    if not isinstance(members, list):
        raise TeamError("メンバーはリストで指定してください")
    known = _console_ids()
    cleaned: list[dict] = []
    seen: set[str] = set()
    for item in members:
        if not isinstance(item, dict):
            raise TeamError("メンバーの形が正しくありません")
        sid = item.get("session_id")
        role = item.get("role")
        if not isinstance(sid, str) or sid not in known:
            raise TeamError("Nexarch のコンソールだけメンバーにできます", 400)
        if role not in ROLES:
            raise TeamError(f"役割は {' / '.join(ROLES)} のどれかにしてください")
        if sid in seen:
            raise TeamError("同じコンソールを 2 回入れられません")
        seen.add(sid)
        cleaned.append({"session_id": sid, "role": role})
    if sum(1 for m in cleaned if m["role"] == "leader") > 1:
        raise TeamError("リーダーは 1 人までです")
    with _lock:
        teams = _load()
        team = _find_team(teams, team_id)
        removed = {m["session_id"] for m in team.get("members", [])} - seen
        team["members"] = cleaned
        _save(teams)
        others = {m["session_id"] for t in teams if t is not team for m in t.get("members", [])}
    for sid in removed - others:
        _clear_role(sid)
    for member in cleaned:
        try:
            manager.tag_session(member["session_id"], {"role": member["role"]})
        except (KeyError, OSError, RuntimeError, ValueError) as exc:
            logger.warning("役割の印を付けられませんでした（%s）: %s", member["session_id"], exc)
    return team


# ---------------------------------------------------------------- タスク

def _check_assignee(team: dict, assignee: object) -> str:
    if assignee in (None, ""):
        return ""
    if not isinstance(assignee, str) or assignee not in {m["session_id"] for m in team.get("members", [])}:
        raise TeamError("担当はこのチームのメンバーから選んでください")
    return assignee


def is_unverified(task: dict) -> bool:
    """報告があり、確認（verify）が空、または試験が空・「なし」「未実施」「-」なら未検証。"""
    report = task.get("report")
    if not isinstance(report, dict):
        return False
    verify = str(report.get("verify") or "").strip()
    tests = str(report.get("tests") or "").strip().lower()
    return not verify or tests in _NONE_WORDS


def _task_view(team: dict, task: dict) -> dict:
    return {**task, "warnings": _warnings(team, task), "unverified": is_unverified(task)}


def set_notify(team_id: str, enabled: object) -> dict:
    if not isinstance(enabled, bool):
        raise TeamError("通知の入り切りは真偽で指定してください")
    with _lock:
        teams = _load()
        team = _find_team(teams, team_id)
        team["notify"] = enabled
        _save(teams)
    return team


def create_task(team_id: str, fields: dict, by: str) -> dict:
    title = clean_line(fields.get("title", ""), "タイトル", TITLE_MAX)
    goal = clean_text(fields.get("goal", ""), "目的")
    acceptance = clean_text(fields.get("acceptance", ""), "受け入れ条件")
    files = clean_files(fields.get("files", []))
    with _lock:
        teams = _load()
        team = _find_team(teams, team_id)
        assignee = _check_assignee(team, fields.get("assignee", ""))
        task = {
            "id": int(team.get("next_task_id") or 1), "title": title, "goal": goal,
            "acceptance": acceptance, "files": files, "assignee": assignee, "status": "todo",
            "report": None, "created_at": _now(), "updated_at": _now(), "history": [],
        }
        team["next_task_id"] = task["id"] + 1
        team.setdefault("tasks", []).append(task)
        _event(task, "作成", by)
        _save(teams)
        return _task_view(team, task)


def update_task(team_id: str, task_id: int, fields: dict, by: str) -> dict:
    with _lock:
        teams = _load()
        team = _find_team(teams, team_id)
        task = _find_task(team, task_id)
        changed: list[str] = []
        if "title" in fields:
            task["title"] = clean_line(fields["title"], "タイトル", TITLE_MAX)
            changed.append("タイトル")
        if "goal" in fields:
            task["goal"] = clean_text(fields["goal"], "目的")
            changed.append("目的")
        if "acceptance" in fields:
            task["acceptance"] = clean_text(fields["acceptance"], "受け入れ条件")
            changed.append("受け入れ条件")
        if "files" in fields:
            task["files"] = clean_files(fields["files"])
            changed.append("担当ファイル")
        if "assignee" in fields and fields["assignee"] != task.get("assignee"):
            task["assignee"] = _check_assignee(team, fields["assignee"])
            changed.append("担当")
        if "status" in fields and fields["status"] != task.get("status"):
            if fields["status"] not in STATUSES:
                raise TeamError(f"状態は {' / '.join(STATUSES)} のどれかにしてください")
            _event(task, f"状態 {task['status']} → {fields['status']}", by)
            task["status"] = fields["status"]
        if changed:
            _event(task, "変更: " + "・".join(changed), by)
        _save(teams)
        return _task_view(team, task)


def delete_task(team_id: str, task_id: int) -> dict:
    with _lock:
        teams = _load()
        team = _find_team(teams, team_id)
        task = _find_task(team, task_id)
        team["tasks"].remove(task)
        _save(teams)
        return task


# ---------------------------------------------------------------- 送り込み・取り込み

def dispatch_text(task: dict) -> str:
    files = ", ".join(task.get("files") or []) or "指定なし"
    return (
        f"【Nexarch タスク #{task['id']}】{task['title']}\n"
        f"目的: {task.get('goal') or '（なし）'}\n"
        f"受け入れ条件: {task.get('acceptance') or '（なし）'}\n"
        f"担当ファイル: {files}\n"
        "終わったら最後に次の 5 行で報告してください:\n"
        "完了報告: <何をしたか 1 行>\n"
        "変更ファイル: <パスを , で>\n"
        "試験: <実行した試験と結果>\n"
        "確認: <実行したコマンドと、その結果の要点>\n"
        "残り: <残っていること。無ければ なし>"
    )


def _assignee_console(task: dict) -> dict:
    sid = task.get("assignee") or ""
    if not sid:
        raise TeamError("担当が決まっていません")
    row = _console_ids().get(sid)
    if row is None:
        raise TeamError("担当のコンソールが閉じています", 409)
    return row


def dispatch_task(team_id: str, task_id: int, by: str) -> dict:
    with _lock:
        team = _find_team(_load(), team_id)
        task = _find_task(team, task_id)
        if task["status"] == "done":
            raise TeamError("完了したタスクは送れません", 409)
        row = _assignee_console(task)
        state = row.get("commander_state") or (status.WAITING if row.get("alive", True) else status.EXITED)
        text = dispatch_text(task)
    if state == status.EXITED:
        raise TeamError("担当のコンソールは終了しています", 409)
    if state == status.WORKING:
        raise TeamError("作業中です。終わってから送ります", 409)
    sid = str(row["id"])
    try:
        manager.inject_input(sid, text, submit=True)
    except (KeyError, OSError, RuntimeError, ValueError) as exc:
        raise TeamError(f"担当のコンソールへ送れませんでした: {exc}", 409) from exc
    with _lock:
        teams = _load()
        team = _find_team(teams, team_id)
        task = _find_task(team, task_id)
        task["status"] = "doing"
        for key in ("stalled_at", "limited_at", "closed_noted", "notified"):
            task.pop(key, None)  # 新しい作業として見張りを数え直す
        _event(task, "担当へ送信", by)
        _save(teams)
        return _task_view(team, task)


_PREFIX = r"^[\s>│|●•·\-\*]*"
_REPORT_RE = {
    "summary": re.compile(_PREFIX + r"完了報告\s*[:：]\s*(.*?)\s*$"),
    "changed_files": re.compile(_PREFIX + r"変更ファイル\s*[:：]\s*(.*?)\s*$"),
    "tests": re.compile(_PREFIX + r"試験\s*[:：]\s*(.*?)\s*$"),
    "verify": re.compile(_PREFIX + r"確認\s*[:：]\s*(.*?)\s*$"),
    "remaining": re.compile(_PREFIX + r"残り\s*[:：]\s*(.*?)\s*$"),
}
_PLACEHOLDER = re.compile(r"^<[^<>]*>$")
_NONE_WORDS = {"", "なし", "無し", "ない", "未実施", "-", "none", "n/a"}


def parse_report(text: str) -> dict | None:
    """画面の文字から、最後の「完了報告:」で始まる 5 行（確認の行が無い古い 4 行も可）の塊を読む。無ければ None。

    送った文面の見本（`完了報告: <何をしたか 1 行>`）が画面に残っているので、`<…>` のままの塊は
    報告とみなさない。
    """
    lines = text.splitlines()
    for start in range(len(lines) - 1, -1, -1):
        first = _REPORT_RE["summary"].match(lines[start])
        if not first:
            continue
        values = {"summary": first.group(1)}
        cursor = start + 1
        for key in ("changed_files", "tests", "verify", "remaining"):
            while cursor < len(lines) and not lines[cursor].strip():
                cursor += 1  # 空行は読み飛ばす
            matched = _REPORT_RE[key].match(lines[cursor]) if cursor < len(lines) else None
            if not matched:
                if key == "verify":
                    values["verify"] = ""  # 古い 4 行の形（確認の行なし）
                    continue
                break
            values[key] = matched.group(1)
            cursor += 1
        else:
            if _PLACEHOLDER.match(values["summary"]):
                continue  # 見本のまま
            return _normalize_report(values)
    return None


def _split_files(value: str) -> list[str]:
    if value.strip().lower() in _NONE_WORDS:
        return []
    return [p for p in (s.strip() for s in re.split(r"[,、]", value)) if p][:FILES_MAX]


def _normalize_report(values: dict) -> dict:
    changed = values.get("changed_files", [])
    if isinstance(changed, str):
        changed = _split_files(changed)
    return {
        "summary": str(values.get("summary", ""))[:REPORT_FIELD_MAX],
        "changed_files": [str(p)[:FILE_MAX] for p in changed][:FILES_MAX],
        "tests": str(values.get("tests", ""))[:REPORT_FIELD_MAX],
        "verify": str(values.get("verify", ""))[:REPORT_FIELD_MAX],
        "remaining": str(values.get("remaining", ""))[:REPORT_FIELD_MAX],
    }


def collect_report(team_id: str, task_id: int, by: str) -> dict:
    with _lock:
        task = _find_task(_find_team(_load(), team_id), task_id)
        sid = task.get("assignee") or ""
        if not sid:
            raise TeamError("担当が決まっていません")
        if sid not in _console_ids():
            raise TeamError("担当のコンソールが閉じています", 409)
    report = parse_report(manager.capture_text(sid, CAPTURE_LINES))
    if report is None:
        raise TeamError("まだ報告がありません", 404)
    return _store_report(team_id, task_id, report, by, "報告を取り込み（画面から）")


def put_report(team_id: str, task_id: int, fields: dict, by: str) -> dict:
    """人が手で報告を入れる。"""
    summary = clean_line(fields.get("summary", ""), "完了報告", REPORT_FIELD_MAX)
    changed = clean_files(fields.get("changed_files", []))
    tests = clean_text(fields.get("tests", ""), "試験", REPORT_FIELD_MAX)
    verify = clean_text(fields.get("verify", ""), "確認", REPORT_FIELD_MAX)
    remaining = clean_text(fields.get("remaining", ""), "残り", REPORT_FIELD_MAX)
    report = {"summary": summary, "changed_files": changed, "tests": tests, "verify": verify, "remaining": remaining}
    return _store_report(team_id, task_id, report, by, "報告を手で入力")


def _store_report(team_id: str, task_id: int, report: dict, by: str, event: str) -> dict:
    with _lock:
        teams = _load()
        team = _find_team(teams, team_id)
        task = _find_task(team, task_id)
        task["report"] = report
        task["status"] = "review"
        _event(task, event, by)
        _save(teams)
        return _task_view(team, task)


# ---------------------------------------------------------------- 一覧と見張り

def list_teams() -> list[dict]:
    """全チーム。タスクに担当の状態（assignee_state）と、doing の報告の有無（report_ready）を添える。

    capture は 1 回の一覧につき担当ごとに 1 回。コンソールが閉じていれば assignee_state は "closed"。
    """
    with _lock:
        teams = _load()
    rows = _console_ids() if any(t.get("tasks") or t.get("members") for t in teams) else {}
    captured: dict[str, bool] = {}
    out: list[dict] = []
    for team in teams:
        tasks = []
        for task in team.get("tasks", []):
            sid = task.get("assignee") or ""
            row = rows.get(sid) if sid else None
            view = _task_view(team, task)
            view["assignee_state"] = (row.get("commander_state") or status.WAITING) if row else ("closed" if sid else "")
            ready = False
            if row is not None and task.get("status") == "doing":
                if sid not in captured:
                    try:
                        captured[sid] = parse_report(manager.capture_text(sid, CAPTURE_LINES)) is not None
                    except (KeyError, OSError, RuntimeError, ValueError):
                        captured[sid] = False
                ready = captured[sid]
            view["report_ready"] = ready
            tasks.append(view)
        out.append({**team, "tasks": tasks})
    return out
