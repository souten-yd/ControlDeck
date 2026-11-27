"""Nexarch の AI チームの見張り（T2a・コード・LLM なし）。

60 秒ごとに、作業中（doing）のタスクの担当のコンソールを見て、板へ反映し、通知する。

- 入力待ち（waiting）に戻った: 画面から報告を取り込む（collect_report と同じ処理）→ review・履歴「自動で取り込み」。
  取り込めなければ（報告なしで止まった）`stalled_at` を付ける。入力待ちが続く間は 1 回だけ。
- 作業中（working）に戻った: `stalled_at` を外す。
- 上限で停止（limited）: `limited_at` を付ける（自動再開は C3 のまま。付け替えは T3）。
- コンソールが閉じた: 履歴に 1 回「コンソールが閉じた」。
- 通知: 「報告あり（自動で取り込み）」「報告なしで止まった」「上限で停止」を、タスクごと・種類ごとに 1 回
  ControlDeck の通知先へ送る。タイトルだけで本文は入れない。チームごとに入り切り（notify・既定オン）。
- 誤検知を避ける歯止め: 送った直後（DISPATCH_GRACE 秒）は見ない（送った直後は入力待ちのまま見える）。
  送った文面（【Nexarch タスク #N】）より後ろの画面だけから報告を読む（前のタスクの報告を拾わない）。
根拠: docs/PLAN-nexarch-team.md T2・設計へ取り込むこと 1・6。
"""
from __future__ import annotations

import asyncio
import logging
import re
import time

from app.commander import consoles, status, teams
from app.terminals.manager import manager

logger = logging.getLogger(__name__)

CHECK_INTERVAL = 60
DISPATCH_GRACE = 90

KIND_REPORT = "report"
KIND_STALLED = "stalled"
KIND_LIMITED = "limited"
KIND_BLOCKED = "blocked"
_KIND_TEXT = {
    KIND_REPORT: "の報告が来ました",
    KIND_STALLED: "が報告なしで止まりました",
    KIND_LIMITED: "の担当が上限で止まりました",
    KIND_BLOCKED: "は確認が通らず、自動で直せませんでした",
}
_ROLE_TEXT = {"leader": "リーダー", "developer": "開発", "reviewer": "レビュー", "researcher": "調査", "member": "メンバー"}
_CLI_TEXT = {"claude": "Claude Code", "codex": "Codex", "opencode": "OpenCode"}


def _screen_for(task: dict, text: str) -> str:
    # 再挑戦の文面（【Nexarch タスク #N 再挑戦 k/2】）も送った文面として数える
    found = list(re.finditer(rf"【Nexarch タスク #{task['id']}(?: 再挑戦 \d+/\d+)?】", text))
    return text[found[-1].start():] if found else text


def _console_label(team: dict, row: dict) -> str:
    role = next((m["role"] for m in team.get("members", []) if m["session_id"] == str(row["id"])), "")
    cli = str(row.get("commander_cli") or "")
    parts = [_ROLE_TEXT.get(role, ""), _CLI_TEXT.get(cli, cli)]
    return "・".join(p for p in parts if p)


def message_for(task: dict, kind: str, label: str) -> str:
    """通知の本文。タスクのタイトルだけで、目的・報告・秘密は入れない。"""
    suffix = f"（{label}）" if label else ""
    return f"Nexarch: タスク #{task['id']}『{str(task.get('title', ''))[:60]}』{_KIND_TEXT[kind]}{suffix}"


def tick(now: float | None = None) -> list[dict]:
    """1 回分の見回り。通知する出来事 [{"team","task","kind","title","message"}] を返す（同期）。"""
    now = now or time.time()
    events: list[dict] = []
    # コンソールの一覧（全コンソールの画面の読み取り・ログイン名の確認）は時間がかかるので、
    # 板の排他（teams._lock）の外で先に取る。持ったままだと板の API がその間止まる。
    with teams._lock:
        has_doing = any(task.get("status") == "doing" and task.get("assignee")
                        for t in teams._load() for task in t.get("tasks", []))
    if not has_doing:
        return events
    rows = {str(r["id"]): r for r in consoles.list_consoles()}
    with teams._lock:
        all_teams = teams._load()
        watched = [(t, task) for t in all_teams for task in t.get("tasks", [])
                   if task.get("status") == "doing" and task.get("assignee")]
        if not watched:
            return events
        screens: dict[str, str | None] = {}
        dirty = False
        to_check: list[tuple[str, int]] = []

        def notify(team: dict, task: dict, kind: str, row: dict) -> None:
            notified = task.setdefault("notified", {})
            if notified.get(kind) or team.get("notify", True) is False:
                return
            notified[kind] = True
            events.append({"team": team["id"], "task": task["id"], "kind": kind, "title": "Nexarch",
                           "message": message_for(task, kind, _console_label(team, row))})

        def forget(task: dict, kind: str) -> None:
            task.get("notified", {}).pop(kind, None)

        for team, task in watched:
            sid = task["assignee"]
            row = rows.get(sid)
            if row is None:
                if not task.get("closed_noted"):
                    task["closed_noted"] = True
                    teams._event(task, "コンソールが閉じた", "見張り")
                    dirty = True
                continue
            if task.pop("closed_noted", None):
                dirty = True
            state = row.get("commander_state") or (status.WAITING if row.get("alive", True) else status.EXITED)
            if state == status.WORKING:
                if task.pop("stalled_at", None) is not None:
                    dirty = True
                if task.pop("limited_at", None) is not None:
                    dirty = True
                forget(task, KIND_STALLED)
                forget(task, KIND_LIMITED)
            elif state == status.LIMITED:
                if not task.get("limited_at"):
                    task["limited_at"] = teams._now()
                    teams._event(task, "上限で停止", "見張り")
                    dirty = True
                before = len(events)
                notify(team, task, KIND_LIMITED, row)
                dirty = dirty or len(events) > before
            elif state == status.WAITING:
                if task.pop("limited_at", None) is not None:
                    forget(task, KIND_LIMITED)
                    dirty = True
                if now - float(task.get("updated_at") or 0) < DISPATCH_GRACE:
                    continue
                if sid not in screens:
                    try:
                        screens[sid] = manager.capture_text(sid, teams.CAPTURE_LINES)
                    except (KeyError, OSError, RuntimeError, ValueError):
                        screens[sid] = None
                text = screens[sid]
                if text is None:
                    continue
                report = teams.parse_report(_screen_for(task, text))
                if report is not None:
                    task["report"] = report
                    task["status"] = "review"
                    task.pop("stalled_at", None)
                    teams._event(task, "報告を取り込み（自動で取り込み）", "見張り")
                    teams.mark_checks_running(task)
                    if task.get("checks"):
                        to_check.append((team["id"], task["id"]))
                    notify(team, task, KIND_REPORT, row)
                    dirty = True
                elif not task.get("stalled_at"):
                    task["stalled_at"] = teams._now()
                    teams._event(task, "報告なしで止まった", "見張り")
                    notify(team, task, KIND_STALLED, row)
                    dirty = True
        if dirty:
            teams._save(all_teams)
    if to_check:
        from app.commander import team_checks
        for team_id, task_id in to_check:
            team_checks.schedule(team_id, task_id)
    return events


async def _channels() -> list[tuple[str, str]]:
    from app.database import SessionLocal
    from app.models import NotificationChannel
    from app.security.crypto import decrypt_text

    def load() -> list[tuple[str, str]]:
        with SessionLocal() as db:
            result = []
            for row in db.query(NotificationChannel).filter(NotificationChannel.enabled.is_(True)).all():
                try:
                    result.append((row.channel_type, decrypt_text(row.url_encrypted)))
                except Exception:  # noqa: BLE001 - 復号できない通知先は飛ばす
                    continue
            return result

    return await asyncio.to_thread(load)


async def send(title: str, message: str) -> None:
    """有効な通知先すべて（Web Push など）へ送る。届かなくても見張りは止めない。"""
    try:
        from app.alerts.notify import send_notification

        for channel_type, url in await _channels():
            await send_notification(channel_type, url, title, message, kind="team_task")
    except Exception:  # noqa: BLE001
        logger.exception("Nexarch のチームの通知を送れませんでした")


async def run_once(now: float | None = None) -> list[dict]:
    events = await asyncio.to_thread(tick, now)
    for event in events:
        await send(event["title"], event["message"])
    return events


async def watch_loop() -> None:
    while True:
        await asyncio.sleep(CHECK_INTERVAL)
        try:
            events = await run_once()
            if events:
                logger.info("Nexarch のチーム: %d 件の出来事を通知しました", len(events))
        except Exception:  # noqa: BLE001 - 見回りの失敗で loop を止めない
            logger.exception("Nexarch のチームの見回りに失敗しました")
