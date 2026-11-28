"""AI Commander の上限からの自動再開（C3・LLM なし）。

上限で止まったコンソール（C2 の判定 LIMITED）の上限の行から回復の時刻を読み、時刻が来たら
同じセッションへ「続けてください」を送る（Terminal の automation と同じ inject_input）。

- 送る文言: コンソールごとの設定（@cd-commander-resume_message）→ 全体の設定 → 既定「続けてください」。
- 時刻の読み取り: Claude「resets 10:20pm (Asia/Tokyo)」「resets Nov 30, 12am (Asia/Tokyo)」、
  Codex「try again at Nov 28th, 2026 3:05 AM」「try again in 2 hours 5 minutes」。
  読めないときは RETRY_UNKNOWN ごとに試す。
- 歯止め: 送る直前に画面を読み直し、上限で止まったままのときだけ送る。送ったら MIN_INTERVAL は
  次を送らない（上限がまだなら同じ時刻の文言が残るため）。1 回の停止につき MAX_ATTEMPTS 回まで。
  止まっていなければ記録を消す（次の停止で数え直す）。
- 記録は tmux の user option（@cd-commander-resume_*）。ControlDeck を再起動しても残る。
- コンソールごとに止められる（@cd-commander-autoresume=off）。送ったら監査ログに残す。
根拠: docs/PLAN-ai-commander.md C3。上限の文言は C0 の実機の記録（c1-spec.md）。
"""
from __future__ import annotations

import asyncio
import logging
import re
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app.commander import status

logger = logging.getLogger(__name__)

CHECK_INTERVAL = 60
GRACE = 60                 # 回復の時刻ちょうどは外しやすいので少し待つ
RETRY_UNKNOWN = 30 * 60    # 時刻が読めないとき
MIN_INTERVAL = 10 * 60     # 送った後、次を送るまで
MAX_ATTEMPTS = 10
# 送る文言はコンソールごとの設定 → 全体の設定（commander/settings.py）→ 既定「続けてください」

_MONTHS = {m: i for i, m in enumerate(
    ("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"), start=1)}


def _tz(name: str | None):
    if name:
        try:
            return ZoneInfo(name.strip())
        except (ZoneInfoNotFoundError, ValueError):
            pass
    return datetime.now().astimezone().tzinfo


def _hour24(hour: int, ampm: str) -> int:
    hour %= 12
    return hour + 12 if ampm.lower() == "pm" else hour


def parse_reset(text: str, now: datetime | None = None) -> datetime | None:
    """上限の行から回復の時刻（aware datetime）。読めなければ None。"""
    now = now or datetime.now().astimezone()
    # Claude: resets Nov 30, 12am (Asia/Tokyo)
    m = re.search(r"resets ([A-Z][a-z]{2}) (\d{1,2}),? (\d{1,2})(?::(\d{2}))?\s?([ap]m)(?: \(([^)]+)\))?", text, re.I)
    if m and m[1].lower()[:3] in _MONTHS:
        tz = _tz(m[6])
        local_now = now.astimezone(tz)
        when = datetime(local_now.year, _MONTHS[m[1].lower()[:3]], int(m[2]),
                        _hour24(int(m[3]), m[5]), int(m[4] or 0), tzinfo=tz)
        if when < local_now - timedelta(days=1):
            when = when.replace(year=when.year + 1)
        return when
    # Claude: resets 10:20pm (Asia/Tokyo)
    m = re.search(r"resets (\d{1,2})(?::(\d{2}))?\s?([ap]m)(?: \(([^)]+)\))?", text, re.I)
    if m:
        tz = _tz(m[4])
        local_now = now.astimezone(tz)
        when = local_now.replace(hour=_hour24(int(m[1]), m[3]), minute=int(m[2] or 0), second=0, microsecond=0)
        if when <= local_now - timedelta(minutes=1):
            when += timedelta(days=1)
        return when
    # Codex: try again at Nov 28th, 2026 3:05 AM
    m = re.search(r"try again at ([A-Z][a-z]{2})[a-z]* (\d{1,2})(?:st|nd|rd|th)?,? (?:(\d{4}) )?(\d{1,2}):(\d{2})\s?([AP]M)",
                  text, re.I)
    if m and m[1].lower()[:3] in _MONTHS:
        tz = _tz(None)
        local_now = now.astimezone(tz)
        year = int(m[3]) if m[3] else local_now.year
        return datetime(year, _MONTHS[m[1].lower()[:3]], int(m[2]), _hour24(int(m[4]), m[6]), int(m[5]), tzinfo=tz)
    # Codex など: try again in 2 hours 5 minutes / in 45 minutes
    m = re.search(r"try again in ((?:\d+\s*(?:days?|hours?|hrs?|minutes?|mins?|seconds?|secs?)[ ,and]*)+)", text, re.I)
    if m:
        total = 0
        for amount, unit in re.findall(r"(\d+)\s*(day|hour|hr|minute|min|second|sec)", m[1], re.I):
            total += int(amount) * {"day": 86400, "hour": 3600, "hr": 3600, "minute": 60, "min": 60,
                                    "second": 1, "sec": 1}[unit.lower()]
        return now + timedelta(seconds=total) if total else None
    return None


def plan(row: dict, now: float) -> dict:
    """1 つのコンソールの、次に送る時刻と理由。{"action": "send"|"wait"|"clear"|"skip", "due": epoch|None}。

    row は list_consoles の行（commander_state・commander_detail・commander_resume_* の印）。
    """
    if row.get("commander_autoresume") == "off":
        return {"action": "skip", "due": None}
    if row.get("commander_state") != status.LIMITED:
        has_marks = any(row.get(f"commander_resume_{k}") for k in ("at", "count", "last"))
        return {"action": "clear" if has_marks else "skip", "due": None}
    count = int(row.get("commander_resume_count") or 0)
    last = float(row.get("commander_resume_last") or 0)
    if count >= MAX_ATTEMPTS:
        return {"action": "skip", "due": None}
    reset = parse_reset(str(row.get("commander_detail") or ""))
    if reset is not None:
        due = reset.timestamp() + GRACE
    else:
        first_seen = float(row.get("commander_resume_at") or 0) or now + RETRY_UNKNOWN
        due = first_seen if not last else last + RETRY_UNKNOWN
    if last:
        due = max(due, last + MIN_INTERVAL)
    return {"action": "send" if now >= due else "wait", "due": due}


def tick(now: float | None = None) -> list[str]:
    """1 回分の見回り。送ったセッションの id を返す（同期・tmux を呼ぶので thread で動かす）。

    ついでに、ロックしたコンソールが外から消えていないかも見る（commander/locks.py。
    新しい見張りの loop は作らない。失敗でこちらの再開を止めない）。
    """
    from app.commander import consoles
    from app.terminals.manager import manager

    now = now or time.time()
    sent: list[str] = []
    for row in consoles.list_consoles():
        sid = str(row["id"])
        decision = plan(row, now)
        try:
            if decision["action"] == "clear":
                manager.clear_marks(sid, ("resume_at", "resume_count", "resume_last"))
            elif decision["action"] == "wait":
                due = str(int(decision["due"]))
                if row.get("commander_resume_at") != due:
                    manager.tag_session(sid, {"resume_at": due})
            elif decision["action"] == "send":
                # 送る直前に読み直す（この 1 分の間に利用者が続けていたら送らない）
                text = manager.capture_text(sid, status.CAPTURE_LINES)
                if status.classify(str(row["commander_cli"]), text)["state"] != status.LIMITED:
                    continue
                from app.commander import settings as commander_settings

                manager.inject_input(sid, commander_settings.message_for(row), submit=True)
                count = int(row.get("commander_resume_count") or 0) + 1
                manager.tag_session(sid, {"resume_count": str(count), "resume_last": str(int(now)),
                                          "resume_at": str(int(now + MIN_INTERVAL))})
                _audit(sid, row, count)
                sent.append(sid)
        except (KeyError, OSError, RuntimeError, ValueError) as exc:
            logger.warning("AI Commander の自動再開でセッション %s を扱えませんでした: %s", sid, exc)
    try:
        from app.commander import locks

        events = locks.check(now=now)
        if events:
            logger.info("AI Commander: ロックしたコンソールの外部消失を %d 件扱いました", len(events))
    except Exception:  # noqa: BLE001 - 鍵の見回りの失敗で再開の見回りを止めない
        logger.exception("AI Commander のロックしたコンソールの見回りに失敗しました")
    return sent


def _audit(sid: str, row: dict, count: int) -> None:
    from app.audit import service as audit
    from app.database import SessionLocal

    try:
        with SessionLocal() as db:
            audit.record(db, "commander.console.autoresume", user=None, resource_type="commander_console",
                         resource_id=sid, metadata={"cli": row.get("commander_cli", ""),
                                                    "account": row.get("commander_account", ""),
                                                    "attempt": count,
                                                    "detail": str(row.get("commander_detail") or "")[:160]})
    except Exception:  # noqa: BLE001 - 記録できなくても再開は止めない
        logger.exception("自動再開の監査ログを書けませんでした")


async def resume_loop() -> None:
    """CHECK_INTERVAL ごとに見回る（ControlDeck の起動中ずっと）。"""
    while True:
        await asyncio.sleep(CHECK_INTERVAL)
        try:
            sent = await asyncio.to_thread(tick)
            if sent:
                logger.info("AI Commander: 上限から自動で再開を送りました: %s", ", ".join(sent))
        except Exception:  # noqa: BLE001 - 見回りの失敗で loop を止めない
            logger.exception("AI Commander の自動再開の見回りに失敗しました")
