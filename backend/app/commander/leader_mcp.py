"""Nexarch のチームリーダーの MCP サーバー `nexarch_team`（T3c・コード）。

チームリーダー（ヘッドレスで長く動く LLM）は、この道具で Nexarch を動かす。**状態は Nexarch が持つ**（計画・仕事・実行・結果は
すべて teams.json）ので、チームリーダーが上限で止まっても、別の endpoint が `team_status` で「ここまでの計画と状態」を受け取って続けられる。

- 口: HTTP `/api/v1/commander/leader-mcp/tools`・`/call`（既存の agent_mcp と同じ形。stdio の橋渡し addon_mcp_bridge.py がそのまま使える）。
  token は実行（チームリーダーの 1 回の起動）ごと・チームに縛る・期限つき（8 時間）。token の subject は `nexarch-team:<team>:<n>`、
  n は何回目の起動か。チームのチームリーダーの実行が走っていて n が今のものでなければ断る（再開後に古い token は使えない）。
  loopback からだけ受ける。別のチームは触れない（token のチームだけ）。
- 道具: team_status・write_plan・research・delegate・wait・read_result・run_checks・integrate・ask_user・wait_answer・report・finish。
  引数は厳密に検証（知らない引数は断る・長さの上限・段は 4 つだけ）。結果の大きさにも上限。
- 担い手は段（tier: explore・implement・review・hard）で振る。どの endpoint を使うかはコード（役割の chain＝T3a/T3b の自動の振り分け）が決める。
  段に endpoint が無ければチームリーダー自身の endpoint。implement と hard は git の worktree＋ブランチで書き、explore と review は
  チームのブランチの worktree を読むだけ（書かせない指示＋CLI の読み取りの設定）。run_id は板のタスク番号。
- 本文・出力・鍵・token をログ・通知に出さない（呼び出しの履歴 mcp_log には道具の名前と成否だけ）。
根拠: 利用者（2026-11-27）「一文でリーダーに依頼したら、調査し、計画を立て、必要な実装項目を振り分け、個別に実装・評価させ、統合して評価・試験する」
（Opus が Haiku・Sonnet を使う流れの模倣。LLM は横断的に）と docs/PLAN-nexarch-team.md T3c。
"""
from __future__ import annotations

import asyncio
import fnmatch
import logging
import os
import re
import secrets
import threading
import time
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Header, HTTPException, Request
from pydantic import BaseModel, Field

from app.addons import tokens
from app.commander import team_dispatch, team_git, team_roles, team_runner, teams, workdir
from app.security.localhost import is_loopback

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/commander/leader-mcp", tags=["commander"])

TOKEN_TTL = 8 * 60 * 60
SUBJECT_RE = re.compile(r"^nexarch-team:(t[0-9a-f]{8}):(\d{1,3})$")
ITEM_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,24}$")
TIERS = team_roles.TIERS
READONLY_TIERS = ("explore", "review")
WRITE_TIERS = ("implement", "hard")
ITEMS_MAX = 30
PLAN_MAX = 20000
INSTR_MAX = 4000
WAIT_MAX = 600
RUN_IDS_MAX = 10
TIMELINE_MAX = 300
MCP_LOG_MAX = 200
RESULT_TAIL = 1500
QUESTION_MAX = 1000
OPTIONS_MAX = 6
SKIP_DIRS = {".git", "node_modules", ".venv", "venv", "__pycache__", "dist", "build", ".next", ".cache", "target", team_git.WT_DIR}
GREP_MATCHES = 60
GREP_FILE_MAX = 1024 * 1024
FILES_LISTED = 200
POLL = 0.5
_git_lock = threading.Lock()


class ToolError(Exception):
    def __init__(self, message: str, status: int = 422) -> None:
        super().__init__(message)
        self.status = status


# ---------------------------------------------------------------- token

def issue_token(team_id: str, n: int) -> str:
    return tokens.issue("control-deck", subject=f"nexarch-team:{team_id}:{n}", kind="nexarch-team", ttl_seconds=TOKEN_TTL)


def check_token(authorization: str | None) -> tuple[str, int]:
    if not authorization or not authorization.startswith("Bearer "):
        raise ToolError("token が必要です", 401)
    try:
        claims = tokens.verify(authorization.removeprefix("Bearer ").strip(), addon_id="control-deck", kind="nexarch-team",
                               max_ttl_seconds=TOKEN_TTL)
    except tokens.AddonTokenError:
        raise ToolError("token が無効か期限切れです", 401) from None
    match = SUBJECT_RE.fullmatch(str(claims.get("sub") or ""))
    if match is None:
        raise ToolError("token が無効です", 401)
    team_id, n = match.group(1), int(match.group(2))
    with teams._lock:  # noqa: SLF001
        try:
            team = teams._find_team(teams._load(), team_id)  # noqa: SLF001
        except teams.TeamError:
            raise ToolError("チームがありません", 404) from None
        run = team.get("leader_run") if isinstance(team.get("leader_run"), dict) else {}
        if run.get("status") != "running" or int(run.get("n") or 0) != n:
            raise ToolError("このチームリーダーの実行は終わっています（token は今の実行のものではありません）", 403)
    return team_id, n


# ---------------------------------------------------------------- 状態の読み書き

def _mutate(team_id: str, fn):
    with teams._lock:  # noqa: SLF001
        all_teams = teams._load()  # noqa: SLF001
        try:
            team = teams._find_team(all_teams, team_id)  # noqa: SLF001
        except teams.TeamError as exc:
            raise ToolError(str(exc), 404) from exc
        out = fn(team)
        teams._save(all_teams)  # noqa: SLF001
        return out


def _read(team_id: str) -> dict:
    with teams._lock:  # noqa: SLF001
        try:
            return teams._find_team(teams._load(), team_id)  # noqa: SLF001
        except teams.TeamError as exc:
            raise ToolError(str(exc), 404) from exc


def add_timeline(team: dict, kind: str, text: str) -> None:
    """時系列（画面に出る）へ 1 行。team を直接書き換える（保存は呼び出し側）。"""
    line = " ".join(str(text).split())[:300]
    rows = team.setdefault("timeline", [])
    rows.append({"at": teams._now(), "kind": kind, "text": line})  # noqa: SLF001
    del rows[:-TIMELINE_MAX]


def timeline(team_id: str, kind: str, text: str) -> None:
    try:
        _mutate(team_id, lambda t: add_timeline(t, kind, text))
    except ToolError:
        pass


def _log_call(team_id: str, tool: str, ok: bool, seconds: float) -> None:
    def add(team: dict) -> None:
        rows = team.setdefault("mcp_log", [])
        rows.append({"at": teams._now(), "tool": tool, "ok": ok, "seconds": round(seconds, 2)})  # noqa: SLF001
        del rows[:-MCP_LOG_MAX]

    try:
        _mutate(team_id, add)
    except ToolError:
        pass


def _strict(args: dict, allowed: set[str], required: set[str] = frozenset()) -> None:
    if not isinstance(args, dict):
        raise ToolError("引数はオブジェクトで渡してください")
    extra = set(args) - allowed
    if extra:
        raise ToolError(f"知らない引数です: {sorted(extra)[:5]}（使えるのは {sorted(allowed)}）")
    missing = required - set(args)
    if missing:
        raise ToolError(f"引数が足りません: {sorted(missing)}")


def _str(args: dict, key: str, maximum: int, *, required: bool = True) -> str:
    value = args.get(key, "")
    if value in (None, ""):
        if required:
            raise ToolError(f"{key} を渡してください")
        return ""
    if not isinstance(value, str):
        raise ToolError(f"{key} は文字列です")
    text = "".join(c for c in value if c in "\n\t" or (ord(c) >= 0x20 and ord(c) != 0x7F)).strip()
    if required and not text:
        raise ToolError(f"{key} を渡してください")
    if len(text) > maximum:
        raise ToolError(f"{key} は {maximum} 文字までです")
    return text


def _tier(value: object) -> str:
    if value not in TIERS:
        raise ToolError(f"tier は {' / '.join(TIERS)} のどれかです")
    return str(value)


def _bool(args: dict, key: str, default: bool) -> bool:
    value = args.get(key, default)
    if not isinstance(value, bool):
        raise ToolError(f"{key} は真偽です")
    return value


def _int(args: dict, key: str, low: int, high: int, default: int) -> int:
    value = args.get(key, default)
    if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
        raise ToolError(f"{key} は {low}〜{high} の整数です")
    return value


def _files(value: object) -> list[str]:
    try:
        return teams.clean_files(value or [])
    except teams.TeamError as exc:
        raise ToolError(str(exc)) from exc


def clean_checks(value: object) -> tuple[list[str], list[str]]:
    """確認のコマンドを T2b の許す形（1 行・shlex で割れる・シェルの演算子なし）だけ通す。(通ったもの, 捨てたものの記録)。"""
    from app.commander import team_leader

    if value in (None, ""):
        return [], []
    if not isinstance(value, list):
        raise ToolError("checks は文字列のリストです")
    ok: list[str] = []
    dropped: list[str] = []
    for cmd in value[:30]:
        text = team_leader.valid_check(cmd)
        if text and text not in ok:
            ok.append(text)
        else:
            dropped.append(str(cmd)[:80])
    return ok[:teams.CHECKS_MAX], dropped


# ---------------------------------------------------------------- 作業場所・git

def _workdir(team: dict) -> Path:
    path = teams.ensure_workdir(team["id"])
    try:
        return workdir.resolve_dir(path)
    except (OSError, ValueError) as exc:
        raise ToolError(f"作業場所を使えません: {exc}", 409) from exc


def ensure_git(team_id: str) -> dict:
    """チームの git の準備（初回だけ作る）。保存した dict（enabled など）を返す。"""
    team = _read(team_id)
    info = team.get("git")
    if isinstance(info, dict) and info.get("checked") and (not info.get("enabled") or Path(str(info.get("team_wt") or "")).is_dir()):
        return info
    path = _workdir(team)
    with _git_lock:
        try:
            info = team_git.ensure_repo(str(path), team_id, git_init=team.get("git_init", True) is not False)
        except team_git.GitError as exc:
            info = {"enabled": False, "origin_was_git": False, "base_branch": "", "team_branch": "", "team_wt": "",
                    "reason": str(exc)[:200]}
    info["checked"] = True

    def save(t: dict) -> None:
        t["git"] = info
        add_timeline(t, "git", "git の作業場所を用意しました（ブランチ " + info["team_branch"] + "）" if info["enabled"]
                     else "git は使いません: " + (info.get("reason") or ""))

    _mutate(team_id, save)
    return info


def _review_cwd(team: dict, info: dict) -> str:
    return info["team_wt"] if info.get("enabled") else str(_workdir(team))


# ---------------------------------------------------------------- 仕事（板のタスク）の見え方

def _find(team: dict, run_id: object) -> dict:
    try:
        number = int(str(run_id))
    except ValueError:
        raise ToolError("run_id が正しくありません") from None
    task = next((t for t in team.get("tasks", []) if t.get("id") == number and t.get("leader_managed")), None)
    if task is None:
        raise ToolError(f"run_id {run_id} はこのチームのチームリーダーの仕事にありません", 404)
    return task


def task_state(task: dict) -> str:
    status = task.get("status")
    if status == "todo":
        return "queued"
    if status == "doing":
        return "running"
    if status == "review":
        return "checking" if (task.get("checks_result") or {}).get("status") == "running" else "finished"
    return {"done": "integrated", "blocked": "blocked", "rejected": "rejected", "dropped": "dropped"}.get(str(status), "running")


FINISHED = {"finished", "integrated", "blocked", "rejected", "dropped"}
# 取り下げできる状態（走っている仕事は先に止める）
DROPPABLE = ("todo", "blocked", "rejected", "review")


def drop_tasks(team: dict, task_ids: list[int] | None, reason: str, by: str) -> list[int]:
    """チームリーダーの仕事を取り下げる（status=dropped・理由つき）。task_ids が None なら、取り下げできる全部。
    2026-11-28 利用者「止まっているか・抜けているか・開発が終わっているのに 0 になっている」: 別の仕事でやり直した仕事や
    使わなかった仕事が、赤い警告と段の数に残り続けていた。取り下げは警告にも段の数にも入れない（画面）。"""
    dropped: list[int] = []
    for task in team.get("tasks", []):
        if not task.get("leader_managed") or task.get("status") not in DROPPABLE:
            continue
        if task_ids is not None and int(task["id"]) not in task_ids:
            continue
        task["status"] = "dropped"
        task["dropped_reason"] = reason[:200]
        task.pop("queued_note", None)
        teams._event(task, "取り下げ: " + reason[:100], by)  # noqa: SLF001
        dropped.append(int(task["id"]))
    return dropped


def tool_drop_task(team_id: str, args: dict) -> dict:
    """仕事を取り下げる（別の仕事でやり直した・不要になった）。走っている仕事は取り下げられない（先に終わりを待つ）。"""
    _strict(args, {"run_ids", "reason"}, {"run_ids", "reason"})
    ids = args["run_ids"]
    if not isinstance(ids, list) or not 1 <= len(ids) <= RUN_IDS_MAX:
        raise ToolError(f"run_ids は 1〜{RUN_IDS_MAX} 件のリストです")
    reason = _str(args, "reason", 200)

    def save(team: dict) -> dict:
        wanted = []
        for rid in ids:
            task = _find(team, rid)
            if task.get("status") not in DROPPABLE:
                raise ToolError(f"#{task['id']} は取り下げられません（状態: {task.get('status')}。走っている仕事は終わりを待つ）", 409)
            wanted.append(int(task["id"]))
        done = drop_tasks(team, wanted, reason, "チームリーダー")
        add_timeline(team, "drop", f"仕事 {', '.join('#' + str(i) for i in done)} を取り下げました: {reason[:80]}")
        return {"dropped": done}

    return _mutate(team_id, save)


def _assignee(team: dict, task: dict) -> str:
    member = teams.find_member(team, str(task.get("assignee") or ""))
    return team_dispatch.member_label(member) if member else ""


def _brief(team: dict, task: dict) -> dict:
    return {"run_id": str(task["id"]), "title": task["title"], "tier": task.get("tier") or "", "item_id": task.get("item_id") or "",
            "state": task_state(task), "status": task["status"], "assignee": _assignee(team, task),
            "checks": (task.get("checks_result") or {}).get("status") or "", "branch": task.get("branch") or ""}


def _cut(text: object, limit: int) -> str:
    return str(text or "")[-limit:]


def _result(team: dict, task: dict, info: dict) -> dict:
    out = _brief(team, task)
    run = task.get("run") if isinstance(task.get("run"), dict) else {}
    if task_state(task) not in FINISHED:
        # 走っている間は、保存前の進み具合（最後の行・道具の呼び出し・経過）をメモリから重ねる（team_runner.live_overlay）。
        # 待ちなら、配り手が始められなかった理由を返す。2026-11-28 本番: どちらも空で、チームリーダーが状態を読めなかった。
        from app.commander import team_dispatch, team_runner

        live = team_runner.live_overlay(str(team.get("id") or ""), int(task["id"]), run) if run.get("started_at") else run
        out["last_line"] = _cut(live.get("last_line"), 200)
        if live.get("progress"):
            out["progress"] = [_cut(p, 120) for p in list(live["progress"])[-5:]]
        if run.get("started_at"):
            out["elapsed_s"] = live.get("elapsed")
        why = team_dispatch.wait_reason(str(team.get("id") or ""), int(task["id"]))
        if why and task.get("status") == "todo":
            out["wait_reason"] = why
        return out
    report = task.get("report") if isinstance(task.get("report"), dict) else None
    out["report"] = report
    checks = task.get("checks_result") if isinstance(task.get("checks_result"), dict) else None
    if checks:
        failed = next((r for r in checks.get("results", []) if r.get("exit_code") != 0), None)
        out["checks_result"] = {"status": checks.get("status"), "commands": [r.get("cmd") for r in checks.get("results", [])],
                                **({"failed": {"cmd": failed["cmd"], "exit_code": failed["exit_code"], "tail": _cut(failed.get("tail"), 800)}}
                                   if failed else {})}
    if info.get("enabled") and task.get("branch"):
        try:
            team_git.commit_all(task["cwd"], f"Nexarch: #{task['id']} {task['title']}")
            out["changes"] = team_git.numstat(info, task["branch"])
        except (team_git.GitError, KeyError):
            pass
    else:
        out["changes"] = {"files": (report or {}).get("changed_files", [])[:100]}
    usage = [r.get("usage") for r in task.get("runs") or [] if r.get("usage")]
    out["usage"] = {"input_tokens": sum(int(u.get("input_tokens") or 0) for u in usage),
                    "output_tokens": sum(int(u.get("output_tokens") or 0) for u in usage),
                    "cost_usd": round(sum(float(u.get("cost_usd") or 0) for u in usage), 4)}
    out["attempts"] = int(task.get("attempts") or 0)
    if task.get("status") == "blocked":
        out["blocked_reason"] = task.get("blocked_reason") or ""
        out["tail"] = _cut(run.get("tail"), RESULT_TAIL)
    return out


def usage_total(team: dict) -> dict:
    """チーム全体の使用量の合計（担い手の実行とチームリーダー）。費用の目安。"""
    total = {"input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0}
    rows = [r.get("usage") for t in team.get("tasks", []) for r in t.get("runs") or []] \
        + [r.get("usage") for r in team.get("leader_runs") or []]
    for usage in rows:
        if isinstance(usage, dict):
            total["input_tokens"] += int(usage.get("input_tokens") or 0)
            total["output_tokens"] += int(usage.get("output_tokens") or 0)
            total["cost_usd"] = round(total["cost_usd"] + float(usage.get("cost_usd") or 0), 4)
    return total


# ---------------------------------------------------------------- 道具

def tool_team_status(team_id: str, args: dict) -> dict:
    _strict(args, set())
    team = _read(team_id)
    run = team.get("leader_run") or {}
    plan = team.get("plan") or {}
    pending = [q for q in team.get("questions", []) if q.get("answer") is None]
    roles = {}
    for tier in TIERS:
        role = team_roles.tier_role(team, tier)
        roles[tier] = [team_roles.endpoint_label(e) for e in team_roles.chain(role)] if role else []
    return {
        "goal": run.get("goal") or "", "brief": team.get("brief") or "", "autonomy": team.get("autonomy", "high"),
        "web_research": team.get("web_research", True) is not False, "resumes": int(run.get("resumes") or 0),
        "git": {k: (team.get("git") or {}).get(k) for k in ("enabled", "origin_was_git", "base_branch", "team_branch")},
        "plan": {"markdown": str(plan.get("markdown") or "")[:8000], "items": plan.get("items") or []},
        "tasks": [_brief(team, t) for t in team.get("tasks", []) if t.get("leader_managed")],
        "pending_questions": [{"id": q["id"], "question": q["question"]} for q in pending],
        "answered": [{"id": q["id"], "question": q["question"], "answer": q["answer"]} for q in team.get("questions", [])
                     if q.get("answer") is not None][-10:],
        "timeline": [{"kind": r["kind"], "text": r["text"]} for r in team.get("timeline", [])[-15:]],
        "tier_assignments": roles, "usage": usage_total(team),
    }


def _validate_items(raw: object) -> list[dict]:
    if not isinstance(raw, list) or not 1 <= len(raw) <= ITEMS_MAX:
        raise ToolError(f"items は 1〜{ITEMS_MAX} 件のリストです")
    items: list[dict] = []
    seen: set[str] = set()
    for index, item in enumerate(raw, 1):
        if not isinstance(item, dict):
            raise ToolError(f"items[{index}] の形が正しくありません")
        _strict(item, {"id", "title", "tier", "depends_on", "files", "checks", "instructions"}, {"id", "title", "tier"})
        item_id = item["id"]
        if not isinstance(item_id, str) or not ITEM_ID_RE.fullmatch(item_id):
            raise ToolError(f"items[{index}].id は英数字・_-（24 文字まで）です")
        if item_id in seen:
            raise ToolError(f"items の id が重なっています: {item_id}")
        seen.add(item_id)
        title = _str(item, "title", teams.TITLE_MAX)
        deps = item.get("depends_on") or []
        if not isinstance(deps, list) or not all(isinstance(d, str) for d in deps):
            raise ToolError(f"items[{index}].depends_on は id のリストです")
        checks, dropped = clean_checks(item.get("checks"))
        items.append({"id": item_id, "title": " ".join(title.split()), "tier": _tier(item["tier"]), "depends_on": list(dict.fromkeys(deps)),
                      "files": _files(item.get("files")), "checks": checks, "dropped_checks": dropped,
                      "instructions": _str(item, "instructions", INSTR_MAX, required=False)})
    ids = {i["id"] for i in items}
    for item in items:
        bad = [d for d in item["depends_on"] if d not in ids or d == item["id"]]
        if bad:
            raise ToolError(f"{item['id']} の depends_on が正しくありません: {bad[:3]}")
    # 循環の検出
    graph = {i["id"]: i["depends_on"] for i in items}
    state: dict[str, int] = {}

    def visit(node: str) -> None:
        if state.get(node) == 1:
            raise ToolError("depends_on が循環しています")
        if state.get(node) == 2:
            return
        state[node] = 1
        for dep in graph[node]:
            visit(dep)
        state[node] = 2

    for node in graph:
        visit(node)
    return items


def tool_write_plan(team_id: str, args: dict) -> dict:
    _strict(args, {"plan_markdown", "items"}, {"plan_markdown", "items"})
    markdown = _str(args, "plan_markdown", PLAN_MAX)
    items = _validate_items(args["items"])

    def save(team: dict) -> dict:
        old = {i["id"]: i for i in (team.get("plan") or {}).get("items", [])}
        for item in items:
            prev = old.get(item["id"])
            item["status"] = prev.get("status", "planned") if prev else "planned"
            item["task_id"] = prev.get("task_id") if prev else None
        for gone in set(old) - {i["id"] for i in items}:  # 外した項目でも、振った仕事は残す
            if old[gone].get("task_id"):
                items.append({**old[gone], "removed": True})
        team["plan"] = {"markdown": markdown, "items": items, "updated_at": teams._now()}  # noqa: SLF001
        add_timeline(team, "plan", f"計画を更新（{len(items)} 件）")
        return {"saved": True, "items": len(items), "dropped_checks": [{"id": i["id"], "dropped": i["dropped_checks"]} for i in items if i["dropped_checks"]]}

    return _mutate(team_id, save)


def _code_search(root: Path, query: str) -> dict:
    """作業場所の grep・ファイル一覧（shell なし・上限つき）。query が `files:<glob>` ならファイルの一覧。"""
    if query.startswith("files:"):
        pattern = query[6:].strip() or "*"
        out: list[str] = []
        for current, dirs, files in os.walk(root, followlinks=False):
            dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS)
            for name in sorted(files):
                rel = os.path.relpath(os.path.join(current, name), root)
                if fnmatch.fnmatch(rel, pattern) or fnmatch.fnmatch(name, pattern):
                    out.append(rel)
                    if len(out) >= FILES_LISTED:
                        return {"files": out, "truncated": True}
        return {"files": out, "truncated": False}
    needle = query.lower()
    matches: list[str] = []
    for current, dirs, files in os.walk(root, followlinks=False):
        dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS)
        for name in sorted(files):
            path = Path(current) / name
            try:
                if path.is_symlink() or path.stat().st_size > GREP_FILE_MAX:
                    continue
                data = path.read_bytes()
            except OSError:
                continue
            if b"\0" in data[:2048]:
                continue
            rel = os.path.relpath(path, root)
            for number, line in enumerate(data.decode("utf-8", errors="replace").splitlines(), 1):
                if needle in line.lower():
                    matches.append(f"{rel}:{number}: {line.strip()[:200]}")
                    if len(matches) >= GREP_MATCHES:
                        return {"matches": matches, "truncated": True}
    return {"matches": matches, "truncated": False}


def _web_research(query: str) -> dict:
    from app.security.offline import ExternalResearchBlocked
    from app.workflows import research_tools

    async def go() -> dict:
        if query.startswith(("http://", "https://")):
            page = await research_tools.read_page(query, "", 6000)
            return {"ok": page.get("ok", False), "url": page.get("url"), "title": page.get("title"),
                    "text": str(page.get("text") or "")[:6000], "error": page.get("error"), "note": research_tools.SOURCE_NOTE}
        found = await research_tools.search_web(query, None, "", 8, searx=research_tools.searx_for({}, "", ""))
        return {"results": [{"title": str(r.get("title") or "")[:150], "url": r.get("url"), "snippet": str(r.get("snippet") or "")[:240]}
                            for r in found.get("results", [])[:8]], "notes": found.get("notes", []), "note": research_tools.SOURCE_NOTE}

    try:
        return asyncio.run(go())
    except ExternalResearchBlocked as exc:
        return {"error": str(exc)[:200]}
    except Exception as exc:  # noqa: BLE001 - 検索の失敗で実行を止めない
        logger.warning("チームリーダーの Web の調査に失敗しました: %s", type(exc).__name__)
        return {"error": "Web の調査に失敗しました"}


def _knowledge_research(query: str) -> dict:
    from app.knowledge import context

    async def go() -> dict:
        built, _ = await context.search_and_build(query, consumer="cloud", budget_chars=20000)  # 機密・AI 利用不可は返らない
        return {"mode": built.mode, "text": (built.text or "関係する公開の文書は見つかりませんでした。")[:20000],
                "sources": [{"label": s["label"], "title": s["title"]} for s in built.sources]}

    try:
        return asyncio.run(go())
    except Exception as exc:  # noqa: BLE001
        logger.warning("チームリーダーの知識の検索に失敗しました: %s", type(exc).__name__)
        return {"error": "知識の検索に失敗しました"}


def tool_research(team_id: str, args: dict) -> dict:
    _strict(args, {"query", "scope"}, {"query", "scope"})
    query = _str(args, "query", 500)
    scope = args["scope"]
    team = _read(team_id)
    if scope == "web":
        if team.get("web_research", True) is False:
            raise ToolError("このチームでは Web の調査が禁止されています（設定 web_research が false）", 403)
        if team.get("local_only"):
            raise ToolError("このチームは閉じたチーム（ローカルだけ）なので、Web の調査はできません", 403)
        return _web_research(query)
    if scope == "knowledge":
        return _knowledge_research(query)
    if scope == "code":
        info = team.get("git") or {}
        root = Path(info["team_wt"]) if info.get("enabled") and Path(str(info.get("team_wt") or "")).is_dir() else _workdir(team)
        return _code_search(root, query)
    raise ToolError("scope は web / knowledge / code のどれかです")


def _leader_member(team: dict, role: dict | None, tier: str) -> dict:
    """段に担い手が無いときの、チームリーダー自身の endpoint の自動のメンバー。待ち・実行中の仕事に使われていない人を使い、
    いなければ増やす（段の数の上限は無い。同時の数は max_parallel が決める）。"""
    ep = team_roles.leader_endpoint(team)
    key = team_roles.endpoint_key(ep)
    role_id = role["id"] if role else ""
    used = {t.get("assignee") for t in team.get("tasks", []) if t.get("status") in ("todo", "doing")}
    for member in team.get("members", []):
        if member.get("auto") and member.get("ep") == key and member.get("role_id") == role_id and teams.member_key(member) not in used:
            return member
    member = {"id": "h" + secrets.token_hex(4), "kind": "headless", "cli": ep["cli"], "account": ep["account"],
              "role": team_roles.MEMBER_ROLE_OF_NAME.get(tier, "developer"), "label": tier, "model": ep["model"], "effort": ep["effort"],
              "auto": True, "role_id": role_id, "ep": key}
    team.setdefault("members", []).append(member)
    return member


def _make_task(team_id: str, *, item: dict | None, title: str, instructions: str, tier: str, files: list[str],
               checks: list[str]) -> dict:
    """板に仕事を作る（段→役割→担い手の自動の振り分け）。作った仕事の辞書。"""
    info = ensure_git(team_id)
    team = _read(team_id)
    cwd = ""
    branch = ""
    readonly = tier in READONLY_TIERS
    if info.get("enabled") and readonly:
        cwd = info["team_wt"]
    # worktree はタスク番号が要るので、番号を先に確保して（板の番号を進めて）から作る
    with teams._lock:  # noqa: SLF001
        all_teams = teams._load()  # noqa: SLF001
        team = teams._find_team(all_teams, team_id)  # noqa: SLF001
        number = int(team.get("next_task_id") or 1)
        team["next_task_id"] = number + 1
        teams._save(all_teams)  # noqa: SLF001
    if info.get("enabled") and not readonly:
        try:
            with _git_lock:
                cwd, branch = team_git.create_item_worktree(str(_workdir(team)), info, team_id, number)
        except team_git.GitError as exc:
            raise ToolError(f"worktree を作れませんでした: {exc}", 409) from exc

    def save(team: dict) -> dict:
        role = team_roles.tier_role(team, tier)
        chain = team_roles.chain(role) if role else []
        assignee = ""
        if not chain:  # 段に担い手が無ければチームリーダー自身の endpoint
            assignee = teams.member_key(_leader_member(team, role, tier))
        task = {
            "id": number, "title": title[:teams.TITLE_MAX], "goal": instructions[:teams.TEXT_MAX],
            "acceptance": "", "files": files, "checks": checks, "assignee": assignee, "status": "todo",
            "role": role["id"] if role else "", "report": None, "created_at": teams._now(), "updated_at": teams._now(),  # noqa: SLF001
            "history": [], "leader_managed": True, "tier": tier, "item_id": item["id"] if item else "",
            "readonly": readonly,
        }
        if cwd:
            task["cwd"] = cwd
        if branch:
            task["branch"] = branch
        team.setdefault("tasks", []).append(task)
        teams._event(task, f"作成（チームリーダー・{tier}）", "チームリーダー")  # noqa: SLF001
        if item is not None:
            for planned in (team.get("plan") or {}).get("items", []):
                if planned["id"] == item["id"]:
                    planned["status"] = "delegated"
                    planned["task_id"] = task["id"]
        add_timeline(team, "delegate", f"仕事 #{task['id']}「{task['title']}」を {tier} へ振りました")
        return task

    return _mutate(team_id, save)


def _queue_note(team: dict, task_id: int, note: str) -> dict:
    """走っている・待っている仕事へ指示を覚える。走っていれば今の実行が終わった後にその指示でもう一度、待ちなら次の実行に足す。
    2026-11-28 チームリーダーの要望: 「E2E 修正の note は実行中 409 で配送できず、完了後に再依頼する」。"""
    task = next(x for x in team["tasks"] if x["id"] == task_id)
    key = "queued_note" if task["status"] == "doing" else "resume_note"
    task[key] = "\n\n".join(x for x in (str(task.get(key) or ""), note) if x)[:teams.TEXT_MAX]
    teams._event(task, "チームリーダーが指示を足しました（" + ("今の実行の後にもう一度" if key == "queued_note" else "次の実行に足す") + "）", "チームリーダー")  # noqa: SLF001
    add_timeline(team, "delegate", f"仕事 #{task_id} に指示を足しました（" + ("今の実行の後にもう一度" if key == "queued_note" else "次の実行で") + "）")
    return {"queued": True}


def _redo(team_id: str, task_id: int, note: str) -> dict:
    """振った仕事をもう一度（競合の解消・指摘の反映）。implement は worktree へチームのブランチを取り込んで続きをやらせる。"""
    info = ensure_git(team_id)
    team = _read(team_id)
    task = next(t for t in team["tasks"] if t["id"] == task_id)
    if task.get("status") in ("doing", "todo"):
        # 走っている・待っている仕事の worktree でコミットや取り込みをしない（担い手が書いている最中のファイルを巻き込む）。
        # 指示だけ覚え、今の実行の後（待ちなら次の実行）に渡す（save の中で同じ判定をもう一度する）。
        return {"task": task_id, "conflicts": [], **_mutate(team_id, lambda t: _queue_note(t, task_id, note))}
    conflicts: list[str] = []
    if task.get("branch") and info.get("enabled") and task.get("cwd"):
        with _git_lock:
            team_git.commit_all(task["cwd"], f"Nexarch: #{task_id} 途中")
            conflicts = team_git.start_conflict_merge(task["cwd"], info["team_branch"])
    text = note
    if conflicts:
        text += "\n【競合】チームのブランチを取り込んだところ、次のファイルが競合しています。印を直して git add・コミットまで終えてください: " + ", ".join(conflicts)

    def save(t: dict) -> None:
        task_ = next(x for x in t["tasks"] if x["id"] == task_id)
        if task_["status"] in ("doing", "todo"):   # 確かめてから save までの間に動き出した
            return _queue_note(t, task_id, text)
        for key in ("report", "checks_result", "run", "limited_at", "blocked_reason", "notified", "wait_until"):
            task_.pop(key, None)
        task_["report"] = None
        task_["attempts"] = 0
        task_["status"] = "todo"
        task_["resume_note"] = text[:teams.TEXT_MAX]
        teams._event(task_, "もう一度（チームリーダーの指示）", "チームリーダー")  # noqa: SLF001
        for planned in (t.get("plan") or {}).get("items", []):
            if planned.get("task_id") == task_id:
                planned["status"] = "delegated"
        add_timeline(t, "delegate", f"仕事 #{task_id} をもう一度やらせます")

    _mutate(team_id, save)
    return {"task": task_id, "conflicts": conflicts}


def tool_delegate(team_id: str, args: dict) -> dict:
    _strict(args, {"item_id", "title", "instructions", "tier", "files", "checks", "wait", "note"})
    wait = _bool(args, "wait", False)
    team = _read(team_id)
    if "item_id" in args:
        if any(k in args for k in ("title", "instructions", "tier", "files", "checks")):
            raise ToolError("item_id と一緒に title・instructions・tier・files・checks は渡せません（計画の項目を使います）")
        item_id = _str(args, "item_id", 24)
        items = {i["id"]: i for i in (team.get("plan") or {}).get("items", [])}
        item = items.get(item_id)
        if item is None:
            raise ToolError(f"計画に項目 {item_id} がありません（write_plan で先に書く）", 404)
        if item.get("task_id"):
            note = _str(args, "note", INSTR_MAX, required=False)
            if not note:
                raise ToolError("この項目は振ってあります。やり直すなら note（指摘・指示）を渡してください")
            run = _redo(team_id, int(item["task_id"]), note)
            run_id = str(run["task"])
        else:
            unmet = [d for d in item.get("depends_on", []) if task_state(next((t for t in team["tasks"] if t["id"] == items[d].get("task_id")),
                                                                              {"status": "todo"})) not in FINISHED]
            if unmet:
                raise ToolError(f"先に終わらせる項目があります: {unmet}（depends_on）", 409)
            task = _make_task(team_id, item=item, title=item["title"], instructions=item.get("instructions") or item["title"],
                              tier=item["tier"], files=item.get("files", []), checks=item.get("checks", []))
            run_id = str(task["id"])
    else:
        _strict(args, {"title", "instructions", "tier", "files", "checks", "wait", "note"}, {"title", "instructions", "tier"})
        checks, _dropped = clean_checks(args.get("checks"))
        task = _make_task(team_id, item=None, title=" ".join(_str(args, "title", teams.TITLE_MAX).split()),
                          instructions=_str(args, "instructions", INSTR_MAX), tier=_tier(args["tier"]),
                          files=_files(args.get("files")), checks=checks)
        run_id = str(task["id"])
    team_dispatch.kick(team_id)
    if wait:
        return tool_wait(team_id, {"run_ids": [run_id], "timeout_s": WAIT_MAX})
    return {"run_id": run_id, "state": task_state(next(t for t in _read(team_id)["tasks"] if str(t["id"]) == run_id))}


def _mark_collected(team_id: str, run_ids: list[str]) -> None:
    """explore・review の仕事は、結果を受け取ったら完了にする（統合は不要）。"""
    def save(team: dict) -> None:
        for rid in run_ids:
            task = next((t for t in team["tasks"] if str(t["id"]) == rid), None)
            if task and task.get("status") == "review" and task.get("tier") in READONLY_TIERS \
                    and (task.get("checks_result") or {}).get("status") != "failed":
                task["status"] = "done"
                teams._event(task, "結果を受け取り（完了）", "チームリーダー")  # noqa: SLF001
                for planned in (team.get("plan") or {}).get("items", []):
                    if planned.get("task_id") == task["id"]:
                        planned["status"] = "done"

    _mutate(team_id, save)


def tool_wait(team_id: str, args: dict) -> dict:
    _strict(args, {"run_ids", "timeout_s", "mode"}, {"run_ids"})
    ids = args["run_ids"]
    if not isinstance(ids, list) or not 1 <= len(ids) <= RUN_IDS_MAX or not all(isinstance(i, (str, int)) and not isinstance(i, bool) for i in ids):
        raise ToolError(f"run_ids は 1〜{RUN_IDS_MAX} 件のリストです")
    ids = [str(i) for i in ids]
    timeout = _int(args, "timeout_s", 1, WAIT_MAX, 120)
    mode = args.get("mode", "all")
    if mode not in ("all", "any"):
        raise ToolError("mode は all か any です")
    for rid in ids:
        _find(_read(team_id), rid)
    deadline = time.monotonic() + timeout
    while True:
        team = _read(team_id)
        tasks = [_find(team, rid) for rid in ids]
        done = [task_state(t) in FINISHED for t in tasks]
        stopped = (team.get("leader_run") or {}).get("status") != "running"
        if stopped or (all(done) if mode == "all" else any(done)) or time.monotonic() >= deadline:
            break
        if any(task_state(t) == "queued" for t in tasks):
            team_dispatch.kick(team_id)  # 空きができたのに配り手の見回りを待たない
        time.sleep(POLL)
    info = team.get("git") or {}
    _mark_collected(team_id, [str(t["id"]) for t, d in zip(tasks, done) if d])
    team = _read(team_id)
    return {"results": [_result(team, _find(team, rid), info) for rid in ids], "all_finished": all(done), "timed_out": not all(done)}


def tool_read_result(team_id: str, args: dict) -> dict:
    _strict(args, {"run_id", "what"}, {"run_id", "what"})
    what = args["what"]
    if what not in ("diff", "output_tail", "files", "report_text"):
        raise ToolError("what は diff / output_tail / files / report_text のどれかです")
    team = _read(team_id)
    task = _find(team, args["run_id"])
    info = team.get("git") or {}
    run = task.get("run") if isinstance(task.get("run"), dict) else {}
    if what == "output_tail":
        from app.commander import team_runner

        running = task_state(task) not in FINISHED and run.get("started_at")
        live = team_runner.live_overlay(str(team.get("id") or ""), int(task["id"]), run) if running else run
        out = {"run_id": str(task["id"]), "last_line": _cut(live.get("last_line"), 300), "output_tail": _cut(run.get("tail"), RESULT_TAIL),
               "progress": (live.get("progress") or [])[-5:]}
        if running:
            out["elapsed_s"] = live.get("elapsed")
            out["note"] = "実行中です。output_tail は終わってから入ります（last_line と progress は今の進み具合）"
        return out
    if what == "report_text":
        # 最後の返事の先頭と末尾（長いレビューの報告の前半が output_tail では読めなかった。2026-11-28 チームリーダーの要望）
        report = task.get("report") if isinstance(task.get("report"), dict) else None
        return {"run_id": str(task["id"]), "report": report, "head": str(run.get("head") or ""), "tail": str(run.get("tail") or ""),
                "note": "head が空なら、最後の返事は tail に全部入っています"}
    if what == "files":
        if info.get("enabled") and task.get("branch"):
            team_git.commit_all(task["cwd"], f"Nexarch: #{task['id']} {task['title']}")
            return {"run_id": str(task["id"]), **team_git.numstat(info, task["branch"])}
        return {"run_id": str(task["id"]), "files": ((task.get("report") or {}).get("changed_files") or [])[:100]}
    if not (info.get("enabled") and task.get("branch")):
        return {"run_id": str(task["id"]), "diff": "", "note": "worktree の仕事ではないので差分はありません（files か報告を見る）"}
    team_git.commit_all(task["cwd"], f"Nexarch: #{task['id']} {task['title']}")
    return {"run_id": str(task["id"]), **team_git.diff(info, task["branch"])}


def tool_run_checks(team_id: str, args: dict) -> dict:
    _strict(args, {"where", "commands"}, {"where"})
    where = args["where"]
    if not isinstance(where, (str, int)) or isinstance(where, bool):
        raise ToolError("where は run_id か \"team\" です")
    team = _read(team_id)
    commands, dropped = clean_checks(args.get("commands"))
    if where == "team":
        info = ensure_git(team_id)
        cwd = _review_cwd(team, info)
        if not commands:
            plan_items = (_read(team_id).get("plan") or {}).get("items", [])
            commands = list(dict.fromkeys(c for i in plan_items for c in i.get("checks", [])))[:teams.CHECKS_MAX]
    else:
        task = _find(team, where)
        cwd = str(task.get("cwd") or _workdir(team))
        commands = commands or list(task.get("checks") or [])
    if not commands:
        raise ToolError("実行する確認のコマンドがありません（commands を渡すか、計画の checks に書く）")
    from app.commander import team_checks

    result = team_checks.run_commands(cwd, commands)
    summary = {"status": result["status"], "dropped": dropped,
               "results": [{"cmd": r["cmd"], "exit_code": r["exit_code"], "seconds": r["seconds"], "tail": _cut(r["tail"], 800)}
                           for r in result.get("results", [])]}
    if result.get("message"):
        summary["message"] = result["message"]
    label = {"passed": "合格", "failed": "不合格", "error": "実行できず"}[result["status"]]

    def save(t: dict) -> None:
        if where == "team":
            t["last_team_checks"] = {"status": result["status"], "ran_at": result["ran_at"], "commands": commands}
        add_timeline(t, "checks", f"確認（{'チーム全体' if where == 'team' else '#' + str(where)}）: {label}")

    _mutate(team_id, save)
    return summary


def tool_integrate(team_id: str, args: dict) -> dict:
    _strict(args, {"run_ids"}, {"run_ids"})
    ids = args["run_ids"]
    if not isinstance(ids, list) or not 1 <= len(ids) <= RUN_IDS_MAX:
        raise ToolError(f"run_ids は 1〜{RUN_IDS_MAX} 件のリストです")
    ids = [str(i) for i in ids]
    info = ensure_git(team_id)
    merged: list[str] = []
    refused: list[dict] = []
    conflicts: list[dict] = []
    for rid in ids:
        team = _read(team_id)
        task = _find(team, rid)
        if task.get("tier") not in WRITE_TIERS:
            refused.append({"run_id": rid, "reason": "implement・hard の仕事だけ統合できます"})
            continue
        if task.get("status") == "done":
            merged.append(rid)
            continue
        if task.get("status") != "review" or (task.get("checks_result") or {}).get("status") in ("running", "failed"):
            refused.append({"run_id": rid, "reason": "まだ終わっていないか、確認が通っていません（wait で状態を見る）"})
            continue
        if info.get("enabled") and task.get("branch"):
            with _git_lock:
                try:
                    team_git.commit_all(task["cwd"], f"Nexarch: #{task['id']} {task['title']}")
                    result = team_git.merge_into_team(info, task["branch"], f"Nexarch: 統合 #{task['id']} {task['title']}")
                except team_git.GitError as exc:
                    refused.append({"run_id": rid, "reason": str(exc)[:200]})
                    continue
            if not result["merged"]:
                conflicts.append({"run_id": rid, "files": result["conflicts"]})
                timeline(team_id, "integrate", f"仕事 #{rid} は競合しました（{len(result['conflicts'])} ファイル）")
                continue

        def done(t: dict, rid=rid) -> None:
            tk = _find(t, rid)
            tk["status"] = "done"
            teams._event(tk, "チームのブランチへ統合", "チームリーダー")  # noqa: SLF001
            for planned in (t.get("plan") or {}).get("items", []):
                if planned.get("task_id") == tk["id"]:
                    planned["status"] = "integrated"
            add_timeline(t, "integrate", f"仕事 #{rid}「{tk['title']}」を統合しました")

        _mutate(team_id, done)
        merged.append(rid)
    out: dict[str, Any] = {"merged": merged, "conflicts": conflicts, "refused": refused}
    if conflicts:
        out["hint"] = ("競合した仕事は delegate(item_id, note=…) でもう一度 implement へ戻せます（worktree にチームのブランチを取り込み、"
                       "競合の印を直させる）。")
    if info.get("enabled"):
        out["team_changes"] = team_git.team_numstat(info)
    return out


def tool_ask_user(team_id: str, args: dict) -> dict:
    _strict(args, {"question", "options", "required"}, {"question"})
    question = _str(args, "question", QUESTION_MAX)
    options = args.get("options") or []
    if not isinstance(options, list) or len(options) > OPTIONS_MAX or not all(isinstance(o, str) and 0 < len(o) <= 100 for o in options):
        raise ToolError(f"options は 100 文字までの文字列 {OPTIONS_MAX} 個までです")
    required = _bool(args, "required", False)
    team = _read(team_id)
    if team.get("autonomy", "high") == "high" and not required:
        timeline(team_id, "decision", "利用者には聞かず、最善の判断で進めます: " + question[:150])
        return {"asked": False, "note": "このチームは聞かずに進める設定です。最善の判断で進め、判断の理由を report で 1 行記録してください。"
                                        "外部からの取得・本番データの変更・秘密に関わることだけは required=true で聞けます。"}

    def add(t: dict) -> str:
        qid = "q" + str(len(t.get("questions", [])) + 1)
        t.setdefault("questions", []).append({"id": qid, "question": question, "options": options, "asked_at": teams._now(),  # noqa: SLF001
                                              "answer": None, "answered_at": None})
        add_timeline(t, "ask", "利用者への質問: " + question[:150])
        return qid

    qid = _mutate(team_id, add)
    if team.get("notify", True) is not False:
        team_runner._notify([{"title": "Nexarch", "notify_kind": "team_question",  # noqa: SLF001
                              "message": f"Nexarch: チーム『{team['name'][:40]}』のチームリーダーが質問しています"}])
    return {"asked": True, "question_id": qid, "note": "wait_answer で答えを待てます"}


def tool_wait_answer(team_id: str, args: dict) -> dict:
    _strict(args, {"question_id", "timeout_s"}, {"question_id"})
    qid = _str(args, "question_id", 12)
    timeout = _int(args, "timeout_s", 1, WAIT_MAX, 120)
    deadline = time.monotonic() + timeout
    while True:
        team = _read(team_id)
        question = next((q for q in team.get("questions", []) if q["id"] == qid), None)
        if question is None:
            raise ToolError("その質問はありません", 404)
        if question.get("answer") is not None:
            return {"answered": True, "answer": question["answer"]}
        if (team.get("leader_run") or {}).get("status") != "running" or time.monotonic() >= deadline:
            return {"answered": False, "note": "まだ答えがありません（別の作業を進めてから、もう一度 wait_answer）"}
        time.sleep(POLL)


def answer_question(team_id: str, question_id: str, answer: object) -> dict:
    """利用者の答え（画面から）。"""
    text = _str({"a": answer}, "a", 2000)

    def save(team: dict) -> dict:
        question = next((q for q in team.get("questions", []) if q["id"] == question_id), None)
        if question is None:
            raise ToolError("その質問はありません", 404)
        if question.get("answer") is not None:
            raise ToolError("その質問には答え済みです", 409)
        question["answer"] = text
        question["answered_at"] = teams._now()  # noqa: SLF001
        add_timeline(team, "answer", "利用者の答え: " + text[:150])
        return question

    return _mutate(team_id, save)


def tool_report(team_id: str, args: dict) -> dict:
    _strict(args, {"progress_text"}, {"progress_text"})
    text = " ".join(_str(args, "progress_text", 300).split())
    timeline(team_id, "progress", text)
    return {"ok": True}


def tool_finish(team_id: str, args: dict) -> dict:
    _strict(args, {"summary", "outcome"}, {"summary", "outcome"})
    summary = _str(args, "summary", teams.TEXT_MAX)
    if args["outcome"] not in ("done", "blocked"):
        raise ToolError("outcome は done か blocked です")

    def save(team: dict) -> dict:
        busy = [str(t["id"]) for t in team.get("tasks", []) if t.get("leader_managed") and t.get("status") in ("todo", "doing")]
        team.setdefault("leader_run", {})["finish"] = {"summary": summary, "outcome": args["outcome"], "at": teams._now()}  # noqa: SLF001
        if args["outcome"] == "done":
            # 完了したら、残った待ち・止まった仕事は取り下げ（使わなかった・別の仕事でやり直した）
            left = drop_tasks(team, None, "チームの完了のとき残っていた（使わなかった）", "Nexarch")
            if left:
                add_timeline(team, "drop", f"完了のとき残っていた仕事 {', '.join('#' + str(i) for i in left)} を取り下げました")
        add_timeline(team, "finish", ("完了: " if args["outcome"] == "done" else "止まりました: ") + summary[:150])
        checks = team.get("last_team_checks") if isinstance(team.get("last_team_checks"), dict) else {}
        out = {"ok": True, "still_running": busy, "team_checks": checks.get("status") or "not_run"}
        if args["outcome"] == "done" and checks.get("status") != "passed":
            # 2026-11-28 品質の評価 A2: 取り込む Claude の前提。止めはしないが、結果に残して知らせる
            out["note"] = "チーム全体の確認（run_checks(where=\"team\")）が合格していません。取り込む前に確かめが要ります"
            add_timeline(team, "checks", "注意: チーム全体の確認が合格しないまま完了にしました")
        return out

    return _mutate(team_id, save)


TOOLS = {
    "team_status": tool_team_status, "write_plan": tool_write_plan, "research": tool_research, "delegate": tool_delegate,
    "wait": tool_wait, "read_result": tool_read_result, "run_checks": tool_run_checks, "integrate": tool_integrate,
    "ask_user": tool_ask_user, "wait_answer": tool_wait_answer, "report": tool_report, "finish": tool_finish,
    "drop_task": tool_drop_task,
}

_OBJ = {"type": "object", "additionalProperties": False}
_ITEM = {"type": "object", "additionalProperties": False, "required": ["id", "title", "tier"], "properties": {
    "id": {"type": "string", "maxLength": 24, "description": "項目の id（英数字・_-）"},
    "title": {"type": "string", "maxLength": 120}, "tier": {"type": "string", "enum": list(TIERS)},
    "depends_on": {"type": "array", "items": {"type": "string"}, "description": "先に終わらせる項目の id"},
    "files": {"type": "array", "items": {"type": "string"}}, "checks": {"type": "array", "items": {"type": "string"},
                                                                         "description": "確認のコマンド（1 行・パイプや && なし）"},
    "instructions": {"type": "string", "maxLength": INSTR_MAX, "description": "担い手への指示（目的・受け入れ条件）"}}}
_RUN_IDS = {"type": "array", "items": {"type": "string"}, "minItems": 1, "maxItems": RUN_IDS_MAX}


def tool_defs() -> list[dict]:
    def tool(name: str, description: str, properties: dict, required: list[str] | None = None) -> dict:
        return {"name": name, "description": description,
                "inputSchema": {**_OBJ, "properties": properties, **({"required": required} if required else {})}}

    return [
        tool("team_status", "目標・計画・仕事の一覧と状態・待っている質問・段ごとの担い手を返す。再開したら最初に呼ぶ。", {}),
        tool("write_plan", "計画を保存する（利用者の画面に出る。何度でも更新できる）。items は仕事の項目（tier は explore・implement・review・hard）。",
             {"plan_markdown": {"type": "string", "maxLength": PLAN_MAX}, "items": {"type": "array", "items": _ITEM, "maxItems": ITEMS_MAX}},
             ["plan_markdown", "items"]),
        tool("research", "調べる。scope=web は query が URL ならそのページを読み、それ以外は Web を検索して上位 8 件の題・URL・抜粋を返す"
                         "（チームの設定で Web の調査を切っていると 403）。knowledge は登録資料の検索（機密・AI 利用不可の資料は返らない）。"
                         "code は作業場所の grep（`files:*.py` でファイルの一覧）。",
             {"query": {"type": "string", "maxLength": 500}, "scope": {"type": "string", "enum": ["web", "knowledge", "code"]}},
             ["query", "scope"]),
        tool("delegate", "仕事を担い手へ振る（担い手は段から Nexarch が決める。implement・hard は worktree で書く）。item_id（計画の項目）か "
                         "title+instructions+tier のどちらか（item_id と一緒に他の欄は渡せない。depends_on が終わっていない項目は 409）。"
                         "振り済みの項目に note（指摘・指示）を付けるともう一度やらせる（走っている間なら、今の実行が終わった後にその指示で、待ちなら次の実行に足す）。files は担当ファイル（同じファイルの仕事は同時に走らない）、"
                         "checks は確認のコマンド（1 行・パイプや && なし。合わないものは捨てる）。wait=true なら終わるまで（最大 600 秒）待って wait と同じ結果を返す。"
                         "それ以外は返り値の run_id を wait へ。",
             {"item_id": {"type": "string"}, "title": {"type": "string", "maxLength": 120},
              "instructions": {"type": "string", "maxLength": INSTR_MAX}, "tier": {"type": "string", "enum": list(TIERS)},
              "files": {"type": "array", "items": {"type": "string"}}, "checks": {"type": "array", "items": {"type": "string"}},
              "wait": {"type": "boolean"}, "note": {"type": "string", "maxLength": INSTR_MAX}}),
        tool("wait", "仕事の終わりを待つ（timeout_s の既定は 120 秒・最大 600 秒）。mode=all は全部、any はどれか 1 つが終わるまで。"
             "終わったものは 5 行の報告・確認の合否・変更ファイルと行数・使用量・担い手を返す。時間切れでも全部の状態を返し、timed_out=true にする（止めはしない）。",
             {"run_ids": _RUN_IDS, "timeout_s": {"type": "integer", "minimum": 1, "maximum": WAIT_MAX}, "mode": {"type": "string", "enum": ["all", "any"]}},
             ["run_ids"]),
        tool("read_result", "仕事の結果を読む。what=diff（差分・大きさの上限つき）・output_tail（出力の末尾）・files（変更ファイルと行数）・"
                            "report_text（最後の返事の先頭 8000 字と末尾。長いレビューの報告の前半を読むとき）。",
             {"run_id": {"type": "string"}, "what": {"type": "string", "enum": ["diff", "output_tail", "files", "report_text"]}}, ["run_id", "what"]),
        tool("run_checks", "確認のコマンドを流す。where=run_id（その仕事の worktree）か \"team\"（チームのブランチ全体）。commands を省くと、run_id なら仕事の checks、"
             "team なら計画の全項目の checks。1 行・パイプや && なしの形だけ流し、捨てたものは dropped に返す。返り値は status（passed・failed・error）と"
             "コマンドごとの終了コード・秒数・出力の末尾。",
             {"where": {"type": "string"}, "commands": {"type": "array", "items": {"type": "string"}}}, ["where"]),
        tool("integrate", "終わった implement・hard の仕事をチームのブランチへ merge する。競合したら競合のファイルを返す（delegate の note で implement へ戻せる）。",
             {"run_ids": _RUN_IDS}, ["run_ids"]),
        tool("ask_user", "利用者へ質問する（画面に出て通知される）。聞いたら question_id を返すので wait_answer で待つ。聞かずに進める設定のチームでは聞かずに"
             "asked=false を返す（最善の判断で進め、理由を report に残す）。required=true（外部からの取得・本番データの変更・秘密）はその設定でも聞く。",
             {"question": {"type": "string", "maxLength": QUESTION_MAX}, "options": {"type": "array", "items": {"type": "string"}, "maxItems": OPTIONS_MAX},
              "required": {"type": "boolean"}}, ["question"]),
        tool("wait_answer", "質問の答えを待つ（timeout_s の既定は 120 秒・最大 600 秒）。答えがあれば answered=true と answer、まだなら answered=false"
                            "（別の作業を進めてから、もう一度呼ぶ）。", {"question_id": {"type": "string"}, "timeout_s": {"type": "integer", "minimum": 1, "maximum": WAIT_MAX}},
             ["question_id"]),
        tool("drop_task", "仕事を取り下げる（別の仕事でやり直した・不要になった）。待ち・止まった・差し戻し・確認待ちの仕事だけ"
                          "（走っている仕事は終わりを待つ）。取り下げた仕事は画面の警告と段の数に入らない。finish(done) は残りを自動で取り下げる。",
             {"run_ids": _RUN_IDS, "reason": {"type": "string", "maxLength": 200}}, ["run_ids", "reason"]),
        tool("report", "進み具合を 1 行、画面の時系列へ出す。", {"progress_text": {"type": "string", "maxLength": 300}}, ["progress_text"]),
        tool("finish", "終わり。summary は何をしたか・確認の結果・残り。outcome は done か blocked。まだ走っている仕事があれば still_running に"
                       "その id を返す（止めはしない。待つか、残りとして summary に書く）。", {
            "summary": {"type": "string", "maxLength": teams.TEXT_MAX}, "outcome": {"type": "string", "enum": ["done", "blocked"]}},
             ["summary", "outcome"]),
    ]


def call_tool(team_id: str, name: str, arguments: dict) -> dict:
    func = TOOLS.get(name)
    if func is None:
        raise ToolError("道具が見つかりません", 404)
    started = time.monotonic()
    ok = False
    try:
        result = func(team_id, arguments)
        ok = True
        return result
    finally:
        _log_call(team_id, name, ok, time.monotonic() - started)


# ---------------------------------------------------------------- HTTP の口

class _Call(BaseModel):
    name: str = Field(max_length=64)
    arguments: dict[str, Any] = Field(default_factory=dict)


def _guard(request: Request, authorization: str | None) -> str:
    if not is_loopback(request):
        raise HTTPException(status_code=403, detail="この口は同じ機械からだけ使えます")
    try:
        return check_token(authorization)[0]
    except ToolError as exc:
        raise HTTPException(status_code=exc.status, detail=str(exc)) from exc


@router.get("/tools")
def list_tools(request: Request, authorization: str | None = Header(default=None)):
    _guard(request, authorization)
    return {"tools": tool_defs()}


@router.post("/call")
def call(body: _Call, request: Request, authorization: str | None = Header(default=None)):
    team_id = _guard(request, authorization)
    try:
        return call_tool(team_id, body.name, body.arguments)
    except ToolError as exc:
        raise HTTPException(status_code=exc.status, detail=str(exc)) from exc
