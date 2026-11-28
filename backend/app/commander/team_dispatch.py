"""Nexarch の AI チームの配り手と上限の方針（T3a・コード・LLM なし）。

- 配り手（dispatcher）: 60 秒ごと（見張りの loop）とタスクが終わったときに、待ち（todo）のタスクを空いた担い手へ
  自動で割り当てて走らせる（チームの auto_run・既定オン）。担当が決まっていればその担当、無ければ候補の順（fallback）で空いている担い手。
- 同時並列の決まり（blocker）: 1 チームで同時に走るのは max_parallel（既定 3）まで／担い手ごとに 1 つ／
  担当ファイルが重なるタスクは同時に走らせない（配り手のとき）／ローカル LLM の担い手は local_parallel（既定 1）まで
  （1 枚の GPU を共有するため）。
- 上限で止まったときの方針 on_limit: handoff（既定・次の候補へ作業内容を引き継いで送り直す）か wait（回復を待つ）。
  引き継ぎの本文には前の担当の最後の報告（無ければ進み具合の最後の行）と作業フォルダの git の変更を付ける。
  候補の既定の順: 上限で止まった担当と別の CLI → 同じ CLI の別アカウント → 最後にローカル。同じ担い手は選ばない。
根拠: 利用者（2026-11-27）「チームがトークンが尽きて止まった場合、作業内容を引き継いで自動でチームリーダーが別の CLI を開くか、
待つかを設定で選択できるように」「同時並列処理の場合はどうするのか」と docs/PLAN-nexarch-team.md T3。
"""
from __future__ import annotations

import asyncio
import logging
import threading
import time
from datetime import datetime
from pathlib import Path

from app.commander import consoles, status, team_roles, team_runner, team_watch, teams, workdir
from app.terminals.manager import manager

logger = logging.getLogger(__name__)

RESET_GRACE = 60
_tick_lock = threading.Lock()
KIND_HANDOFF = team_watch.KIND_HANDOFF


# ---------------------------------------------------------------- 表示名・候補

def member_label(member: dict, row: dict | None = None) -> str:
    if teams.is_headless(member):
        name = str(member.get("label") or "")
        base = f"{team_watch._CLI_TEXT.get(str(member.get('cli')), member.get('cli'))}（{member.get('account')}）"  # noqa: SLF001
        return f"{name}・{base}" if name else base
    cli = str((row or {}).get("commander_cli") or "")
    label = str((row or {}).get("commander_label") or "")
    return label or team_watch._CLI_TEXT.get(cli, cli) or "コンソール"  # noqa: SLF001


def _member_cli(member: dict, rows: dict[str, dict]) -> str:
    if teams.is_headless(member):
        return str(member.get("cli") or "")
    return str((rows.get(str(member["session_id"])) or {}).get("commander_cli") or "")


def _member_account(member: dict, rows: dict[str, dict]) -> str:
    if teams.is_headless(member):
        return str(member.get("account") or "")
    return str((rows.get(str(member["session_id"])) or {}).get("commander_account") or "")


def candidates(team: dict, current_key: str, exclude: set[str], rows: dict[str, dict]) -> list[dict]:
    """次の担い手の候補（順位つき）。current_key と exclude の担い手は含めない。

    team.fallback があればその順（載っていないメンバーは後ろ）。無ければ既定: 別の CLI → 同じ CLI の別アカウント → ローカル。
    閉じたチーム（local_only）ではローカル LLM の担い手だけを候補にする。
    """
    members = [m for m in team.get("members", []) if teams.member_key(m) not in exclude | {current_key}]
    if team.get("local_only"):
        members = [m for m in members if not teams.member_local_problem(m)]
    order = [k for k in team.get("fallback", []) if isinstance(k, str)]
    if order:
        rank = {k: i for i, k in enumerate(order)}
        return sorted(members, key=lambda m: rank.get(teams.member_key(m), len(order)))
    current = teams.find_member(team, current_key)
    cur_cli = _member_cli(current, rows) if current else ""

    def group(m: dict) -> int:
        if teams.member_is_local(m):
            return 2
        return 1 if _member_cli(m, rows) == cur_cli else 0

    base = [m for m in members if m.get("role") not in ("leader", "reviewer")] or members
    return sorted(base, key=group)  # sorted は安定。同じ組の中はメンバーの並びのまま


# ---------------------------------------------------------------- 同時並列の決まり

def blocker(team: dict, task: dict, member: dict, *, check_overlap: bool, retry: bool = False) -> str:  # noqa: ARG001
    """今このタスクをこの担い手で走らせられない理由（空なら走らせてよい）。"""
    key = teams.member_key(member)
    others = [t for t in team.get("tasks", []) if t.get("status") == "doing" and t.get("id") != task.get("id")]
    if any(t.get("assignee") == key for t in others):
        return "担当は別のタスクの作業中です（担い手ごとに同時に 1 つ）"
    if len(others) >= int(team.get("max_parallel") or 3):
        return f"同時に走らせる上限（{int(team.get('max_parallel') or 3)}）に達しています"
    if teams.member_is_local(member):
        local = sum(1 for t in others if (m := teams.find_member(team, str(t.get("assignee") or ""))) and teams.member_is_local(m))
        if local >= int(team.get("local_parallel") or 1):
            return "ローカルの担い手の同時の数の上限です（GPU を共有）"
    if check_overlap and task.get("files") and not task.get("cwd"):  # worktree の仕事は隔離されているので重なっても並べる
        for other in others:
            if any(teams._overlaps(a, b) for a in task["files"] for b in other.get("files", [])):  # noqa: SLF001
                return f"#{other['id']} と担当ファイルが重なっています（終わるまで待ち）"
    return ""


def _console_ready(member: dict, rows: dict[str, dict]) -> bool:
    row = rows.get(str(member["session_id"]))
    if row is None or not row.get("alive", True):
        return False
    return (row.get("commander_state") or status.WAITING) == status.WAITING


def _ready(member: dict, rows: dict[str, dict]) -> bool:
    return True if teams.is_headless(member) else _console_ready(member, rows)


# ---------------------------------------------------------------- 引き継ぎの本文

def _workdir_of(team: dict, member: dict | None) -> Path | None:
    try:
        if member is not None and not teams.is_headless(member):
            raw = manager.session_cwd(str(member["session_id"]))
        else:
            raw = str(team.get("workdir") or "")
        return workdir.resolve_dir(raw) if raw else None
    except (KeyError, OSError, RuntimeError, ValueError, AttributeError):
        return None


def _last_work(task: dict, row: dict | None) -> str:
    report = task.get("report")
    if isinstance(report, dict) and report.get("summary"):
        return f"前の報告: {report['summary']}（変更ファイル: {', '.join(report.get('changed_files') or []) or 'なし'}）"
    run = task.get("run")
    if isinstance(run, dict) and run.get("last_line"):
        return f"進み具合の最後の行: {run['last_line']}"
    if row and row.get("commander_last_line"):
        return f"画面の最後の行: {row['commander_last_line']}"
    return "（記録なし）"


def handoff_text(prev_label: str, last_work: str, git: str, why: str = "上限で止まりました") -> str:
    text = (f"【引き継ぎ】前の担当（{prev_label}）が{why}。ここまでの作業: {last_work}。")
    if git:
        text += f"\n作業フォルダの変更:\n{git}\n"
    return text + "続きから仕上げて、同じ 5 行で報告してください。"


def resume_text(team: dict, task: dict, why: str) -> str:
    """同じ担当で続きから再実行するときの本文（中断・回復の後）。

    worktree の仕事（task.cwd）はそこから git の変更を読み、「変更がそのまま残っている」ことを伝える
    （brief D・2026-11-28: 前はチームの作業場所を読んでいたので、worktree で働く担い手に自分の変更が見えなかった）。"""
    cwd = Path(str(task.get("cwd") or "")) if task.get("cwd") else _workdir_of(team, None)
    git = team_runner.git_changes(cwd) if cwd else ""
    text = f"【引き継ぎ】{why}。"
    if task.get("cwd"):
        text += "前回は途中で中断。worktree の変更はそのまま残っている。git status と diff を見て続きから。"
    text += f"ここまでの作業: {_last_work(task, None)}。"
    if git:
        text += f"\n作業フォルダの変更:\n{git}\n"
    return text + "続きから仕上げて、同じ 5 行で報告してください。"


# ---------------------------------------------------------------- 上限の方針

def apply_limit_policy(team_id: str, task_id: int, rows: dict[str, dict] | None = None) -> list[dict]:
    """上限で止まったタスクへ方針（wait / handoff）を適用する。通知する出来事を返す（送るのは呼び出し側）。"""
    events: list[dict] = []
    now = time.time()
    with teams._lock:  # noqa: SLF001
        all_teams = teams._load()  # noqa: SLF001
        try:
            team = teams._find_team(all_teams, team_id)  # noqa: SLF001
            task = teams._find_task(team, task_id)  # noqa: SLF001
        except teams.TeamError:
            return events
        if task.get("status") != "doing" or not task.get("limited_at"):
            return events
        sid = str(task.get("assignee") or "")
        prev = teams.find_member(team, sid)
        if prev is None:
            return events
        headless = teams.is_headless(prev)
        run = task.get("run") if isinstance(task.get("run"), dict) else {}
        if headless and run.get("status") != "limited":
            return events  # まだ実行中（印だけ残っている）
        write_failed = run.get("reason") == "write_failed"
        policy = "handoff" if write_failed else team.get("on_limit", "handoff")
        if rows is None:
            rows = {} if headless else {str(r["id"]): r for r in consoles.list_consoles()}
        elif not rows and not headless:
            rows = {str(r["id"]): r for r in consoles.list_consoles()}
        role = team_roles.find_role(team, task.get("role"))
        role_choice: dict | None = None
        if role is not None:
            prev_ep = team_roles.member_endpoint(prev)
            if prev_ep is not None and not write_failed:  # 上限なら、その担い手は回復の時刻まで使わない（戻りは配り手が判断）
                team_roles.mark_limited(team, prev_ep, run.get("reset_at") if headless else None, now)
            if policy == "handoff":
                role_choice = team_roles.next_after_limit(team, task, role, _limited_endpoint_keys(team, task), now)
                if role_choice is None and not _fallback_left(team, task, sid):
                    teams.runs_close(task, "limited")
                    task["status"] = "blocked"
                    task["blocked_reason"] = "no_handoff"
                    for k in ("limited_at", "wait_until", "wait_noted", "handoff_waiting_noted"):
                        task.pop(k, None)
                    teams._event(task, "止まった（引き継ぎ先なし）", "Nexarch")  # noqa: SLF001
                    events += _notify_once(team, task, team_watch.KIND_NO_HANDOFF, member_label(prev, rows.get(sid)))
                    teams._save(all_teams)  # noqa: SLF001
                    return events
        if role_choice is None and write_failed and not candidates(team, sid, _limited_members(task), rows):
            task["status"] = "blocked"
            task.pop("limited_at", None)
            teams._event(task, "書き込みがすべて失敗・引き継ぎ先なし（自動で直せず）", "Nexarch")  # noqa: SLF001
            events += team_runner._blocked_event(team, task)  # noqa: SLF001
            teams._save(all_teams)  # noqa: SLF001
            return events
        if role_choice is None and (policy == "wait" or _no_candidate(team, task, sid, rows)):
            changed = False
            restart = False
            if headless:
                if not task.get("wait_until"):
                    reset = run.get("reset_at")
                    task["wait_until"] = (float(reset) + RESET_GRACE) if reset else now + team_runner.WAIT_UNKNOWN
                    when = datetime.fromtimestamp(task["wait_until"]).strftime("%H:%M")
                    note = "" if policy == "wait" else "（引き継ぎ先がないため）"
                    teams._event(task, f"回復を待つ（〜{when}）{note}", "Nexarch")  # noqa: SLF001
                    changed = True
                    events += _notify_once(team, task, team_watch.KIND_LIMITED, member_label(prev))
                elif now >= float(task["wait_until"]):
                    task["resume_note"] = resume_text(team, task, "回復したので同じ担当で再開します")
                    task["status"] = "todo"
                    task.pop("limited_at", None)
                    task.pop("wait_until", None)
                    teams.runs_close(task, "limited")
                    teams._event(task, "回復の時刻になったので再実行", "Nexarch")  # noqa: SLF001
                    changed = True
                    restart = True
            elif not task.get("wait_noted"):
                task["wait_noted"] = True
                teams._event(task, "回復を待つ（自動再開に任せる）", "Nexarch")  # noqa: SLF001
                changed = True
            if changed:
                teams._save(all_teams)  # noqa: SLF001
        else:
            restart = False
            choice = role_choice or _pick_ready(team, task, sid, rows)
            if choice is None:
                if not task.get("handoff_waiting_noted"):
                    task["handoff_waiting_noted"] = True
                    teams._event(task, "引き継ぎ先が空くのを待つ", "Nexarch")  # noqa: SLF001
                    teams._save(all_teams)  # noqa: SLF001
                return events
            git_cwd = _workdir_of(team, prev)
            git = team_runner.git_changes(git_cwd) if git_cwd else ""
            text = handoff_text(member_label(prev, rows.get(sid)), _last_work(task, rows.get(sid)), git,
                                "書き込みがすべて失敗しました" if write_failed else "上限で止まりました")
            new_key = teams.member_key(choice)
            prev_label = member_label(prev, rows.get(sid))
            new_label = member_label(choice, rows.get(new_key))
            teams.runs_close(task, "limited")
            task["assignee"] = new_key
            task["status"] = "todo"
            task["resume_note"] = text
            for k in ("limited_at", "wait_noted", "handoff_waiting_noted", "wait_until", "run", "write_fail_retries"):
                task.pop(k, None)
            teams._event(task, f"上限で停止 → {new_label}へ引き継ぎ", "Nexarch")  # noqa: SLF001
            teams._save(all_teams)  # noqa: SLF001
            if team.get("notify", True) is not False:
                events.append({"team": team_id, "task": task_id, "kind": KIND_HANDOFF, "title": "Nexarch",
                               "message": team_watch.message_for(task, KIND_HANDOFF, f"{prev_label} → {new_label}")})
            restart = True
    if restart:
        try:
            teams.dispatch_task(team_id, task_id, "Nexarch")
        except teams.TeamError as exc:
            logger.info("引き継ぎ・再開をすぐには始められませんでした（待ちに戻します）: %s", exc)
    return events


def _no_candidate(team: dict, task: dict, sid: str, rows: dict[str, dict]) -> bool:
    """handoff なのに引き継げる担い手が 1 人もいない（全員が上限で止まった・メンバーが他にいない）。"""
    return team.get("on_limit", "handoff") == "handoff" and not candidates(team, sid, _limited_members(task), rows)


def _limited_members(task: dict) -> set[str]:
    return {str(r.get("member")) for r in task.get("runs") or [] if r.get("result") in ("limited", "write_failed")}


def _limited_endpoint_keys(team: dict, task: dict) -> set[str]:
    """このタスクで上限・書き込み失敗で止まった担い手の endpoint の鍵（同じ endpoint へは戻さない）。"""
    out: set[str] = set()
    for key in _limited_members(task):
        member = teams.find_member(team, key)
        ep = team_roles.member_endpoint(member) if member else None
        if ep is not None:
            out.add(team_roles.endpoint_key(ep))
    return out


def _fallback_left(team: dict, task: dict, sid: str) -> bool:
    """明示した候補の順番（fallback）に、まだ引き継げるメンバーが残っているか。"""
    skip = _limited_members(task) | {sid}
    return any(k not in skip and teams.find_member(team, k) is not None for k in team.get("fallback", []))


def _pick_ready(team: dict, task: dict, sid: str, rows: dict[str, dict]) -> dict | None:
    for member in candidates(team, sid, _limited_members(task), rows):
        if _ready(member, rows) and not blocker(team, task, member, check_overlap=False):
            return member
    return None


def _notify_once(team: dict, task: dict, kind: str, label: str) -> list[dict]:
    notified = task.setdefault("notified", {})
    if notified.get(kind) or team.get("notify", True) is False:
        return []
    notified[kind] = True
    return [{"team": team["id"], "task": task["id"], "kind": kind, "title": "Nexarch",
             "message": team_watch.message_for(task, kind, label)}]


# ---------------------------------------------------------------- 配り手

def _fresh(team_id: str) -> dict | None:
    with teams._lock:  # noqa: SLF001
        return next((t for t in teams._load() if t["id"] == team_id), None)  # noqa: SLF001


def _eligible(team: dict, rows: dict[str, dict]) -> list[dict]:
    order = candidates(team, "", set(), rows)
    return order


def _role_pick(team_id: str, task_id: int) -> tuple[str, dict | None]:
    """役割のタスクの担い手を選ぶ（自動のメンバーを作る・戻りの履歴を残す。保存まで）。"""
    with teams._lock:  # noqa: SLF001
        all_teams = teams._load()  # noqa: SLF001
        try:
            team = teams._find_team(all_teams, team_id)  # noqa: SLF001
            task = teams._find_task(team, task_id)  # noqa: SLF001
        except teams.TeamError:
            return "wait", None
        mode, member = team_roles.pick_for_task(team, task)
        if mode == "pick":
            teams._save(all_teams)  # noqa: SLF001
        return mode, member


# 待ち（todo）のタスクを始められなかった理由（メモリだけ。チームリーダーの wait・read_result と画面へ返す）。
# 2026-11-28 本番: チームリーダーが「実装が待ったまま、理由が分からない」と要望を残した。
_wait_reasons: dict[tuple[str, int], str] = {}


def wait_reason(team_id: str, task_id: int) -> str:
    return _wait_reasons.get((team_id, int(task_id)), "")


def dispatch_team(team_id: str, rows: dict[str, dict] | None = None) -> int:
    """待ち（todo）のタスクを、決まりの範囲で空いた担い手へ割り当てて走らせる。始めた数を返す。"""
    started = 0
    for _ in range(32):
        team = _fresh(team_id)
        if team is None:
            return started
        auto = team.get("auto_run", True) is not False
        # チームリーダーが振った仕事（leader_managed）は、auto_run が切でも配る（T3c）
        todo = sorted((t for t in team.get("tasks", []) if t.get("status") == "todo" and (auto or t.get("leader_managed"))),
                      key=lambda t: t["id"])
        if not todo:
            return started
        if rows is None and any(not teams.is_headless(m) for m in team.get("members", [])):
            rows = {str(r["id"]): r for r in consoles.list_consoles()}
        live_rows = rows or {}
        chosen: tuple[dict, dict] | None = None
        for task in todo:
            if task.get("role") and not task.get("assignee"):
                mode, role_member = _role_pick(team_id, task["id"])
                if mode == "wait":
                    _wait_reasons[(team_id, task["id"])] = "役割の担い手が上限などで止まっていて、引き継ぎ先も空いていません"
                    continue
                if mode == "pick":
                    team = _fresh(team_id) or team  # 自動のメンバーが増えた
                    if role_member is not None and not teams.is_headless(role_member):
                        live_rows = {str(r["id"]): r for r in consoles.list_consoles()}
                        rows = live_rows
                    pool = [role_member]
                else:
                    pool = _eligible(team, live_rows)
            else:
                pool = [teams.find_member(team, str(task["assignee"]))] if task.get("assignee") else _eligible(team, live_rows)
            reason = "担い手がまだ空いていません"
            for member in pool:
                if member is None or not _ready(member, live_rows):
                    continue
                if teams.is_headless(member) and team_runner.is_active(team_id, task["id"]):
                    continue
                why = blocker(team, task, member, check_overlap=True)
                if why:
                    reason = why
                    continue
                chosen = (task, member)
                break
            if chosen:
                _wait_reasons.pop((team_id, task["id"]), None)
            else:
                _wait_reasons[(team_id, task["id"])] = reason
            if chosen:
                break
        if chosen is None:
            return started
        task, member = chosen
        try:
            if str(task.get("assignee") or "") != teams.member_key(member):
                teams.update_task(team_id, task["id"], {"assignee": teams.member_key(member)}, "Nexarch")
            teams.dispatch_task(team_id, task["id"], "Nexarch")
            started += 1
            rows = None if rows is None else {str(r["id"]): r for r in consoles.list_consoles()}
        except teams.TeamError as exc:
            logger.info("待ちのタスクを始められませんでした: %s", exc)
            return started
    return started


def kick(team_id: str) -> int:
    """タスクが終わったときに、すぐ配る（同時に 2 つの配りが走らないよう、取れなければ次の見回りに任せる）。"""
    if not _tick_lock.acquire(blocking=False):
        return 0
    try:
        return dispatch_team(team_id)
    finally:
        _tick_lock.release()


def tick(now: float | None = None) -> list[dict]:  # noqa: ARG001
    """1 回分の配り（同期）。中断の回収 → 上限の方針 → 待ちの割り当て。通知する出来事を返す。"""
    events: list[dict] = []
    if not _tick_lock.acquire(blocking=False):
        return events
    try:
        team_runner.recover_interrupted()
        with teams._lock:  # noqa: SLF001
            all_teams = teams._load()  # noqa: SLF001
        for team in all_teams:
            limited = [t["id"] for t in team.get("tasks", []) if t.get("status") == "doing" and t.get("limited_at")]
            todo = any(t.get("status") == "todo" for t in team.get("tasks", []))
            rows: dict[str, dict] | None = None
            if (limited or todo) and any(not teams.is_headless(m) for m in team.get("members", [])):
                rows = {str(r["id"]): r for r in consoles.list_consoles()}
            for task_id in limited:
                events += apply_limit_policy(team["id"], task_id, rows if rows is not None else {})
            if todo or limited:
                dispatch_team(team["id"], rows)
    finally:
        _tick_lock.release()
    return events


async def run_once() -> list[dict]:
    events = await asyncio.to_thread(tick)
    for event in events:
        await team_watch.send(event["title"], event["message"])
    return events
