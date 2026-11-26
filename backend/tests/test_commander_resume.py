"""AI Commander の上限からの自動再開（C3・LLM なし）の試験。

目的: 上限で止まったコンソールを回復の時刻に「続けて」で再開する。空振り（まだ上限）・送りすぎ・
利用者が続けた後の誤送信を防ぐ歯止めを守る。
根拠: docs/PLAN-ai-commander.md C3。上限の文言は C0 の実機の記録（c1-spec.md）と Codex の表示。
既存の試験に同じ観点は無い（test-catalog で確認。Terminal の automation は条件つきの送信で、上限は扱わない）。
"""
from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from app.commander import resume, status

TOKYO = ZoneInfo("Asia/Tokyo")
NOW = datetime(2026, 11, 27, 20, 0, tzinfo=TOKYO)


def test_parse_claude_and_codex_reset_times():
    """Claude の「resets 10:20pm (Asia/Tokyo)」「resets Nov 30, 12am」・Codex の「try again at/in」。"""
    assert resume.parse_reset("You've hit your 5-hour limit · resets 10:20pm (Asia/Tokyo)", NOW) == \
        datetime(2026, 11, 27, 22, 20, tzinfo=TOKYO)
    # 過ぎた時刻は翌日
    assert resume.parse_reset("resets 7pm (Asia/Tokyo)", NOW) == datetime(2026, 11, 28, 19, 0, tzinfo=TOKYO)
    assert resume.parse_reset("weekly limit · resets Nov 30, 12am (Asia/Tokyo)", NOW) == \
        datetime(2026, 11, 30, 0, 0, tzinfo=TOKYO)
    codex = resume.parse_reset("You've hit your usage limit. try again at Nov 28th, 2026 3:05 AM.", NOW)
    assert (codex.month, codex.day, codex.hour, codex.minute) == (11, 28, 3, 5)
    later = resume.parse_reset("Rate limited. Try again in 2 hours 5 minutes.", NOW)
    assert (later - NOW).total_seconds() == 2 * 3600 + 5 * 60
    assert resume.parse_reset("rate limit exceeded", NOW) is None


def _row(**kw):
    row = {"id": "aa000001", "commander_cli": "claude", "commander_account": "default",
           "commander_state": status.LIMITED, "commander_detail": "resets 10:20pm (Asia/Tokyo)"}
    row.update(kw)
    return row


def test_plan_waits_until_reset_then_sends_with_guards():
    """回復の時刻＋猶予までは待ち、過ぎたら送る。送った後は 10 分空ける・10 回で止める・止めたら送らない。"""
    reset = datetime(2026, 11, 27, 22, 20, tzinfo=TOKYO).timestamp()
    import app.commander.resume as r
    orig = r.parse_reset
    r.parse_reset = lambda text, now=None: orig(text, NOW)
    try:
        assert resume.plan(_row(), reset - 60)["action"] == "wait"
        assert resume.plan(_row(), reset + resume.GRACE)["action"] == "send"
        # 送った直後（まだ上限の文言が残る）は MIN_INTERVAL 空ける
        sent = _row(commander_resume_count="1", commander_resume_last=str(int(reset + 60)))
        assert resume.plan(sent, reset + 120)["action"] == "wait"
        assert resume.plan(sent, reset + 60 + resume.MIN_INTERVAL)["action"] == "send"
        assert resume.plan(_row(commander_resume_count=str(resume.MAX_ATTEMPTS)), reset + 9999)["action"] == "skip"
        assert resume.plan(_row(commander_autoresume="off"), reset + 9999)["action"] == "skip"
    finally:
        r.parse_reset = orig
    # 時刻が読めないときは 30 分ごと
    unknown = _row(commander_detail="rate limit exceeded")
    assert resume.plan(unknown, 1000.0) == {"action": "wait", "due": 1000.0 + resume.RETRY_UNKNOWN}
    assert resume.plan(dict(unknown, commander_resume_at="2000"), 2000.0)["action"] == "send"
    # 止まっていなければ記録を消す（印が無ければ何もしない）
    assert resume.plan(_row(commander_state=status.WORKING, commander_resume_count="2"), 0)["action"] == "clear"
    assert resume.plan(_row(commander_state=status.WAITING), 0)["action"] == "skip"


class FakeManager:
    def __init__(self, screen):
        self.screen = screen
        self.sent = []
        self.tags = {}
        self.cleared = []

    def capture_text(self, sid, lines=60):
        return self.screen

    def inject_input(self, sid, text, *, submit=True):
        self.sent.append((sid, text, submit))
        return len(text)

    def tag_session(self, sid, marks):
        self.tags.setdefault(sid, {}).update(marks)

    def clear_marks(self, sid, keys):
        self.cleared.append((sid, keys))


LIMIT_SCREEN = "● 続けます\n  ⎿  You've hit your 5-hour limit · resets 10:20pm (Asia/Tokyo)\n❯ \n  ? for shortcuts\n"


def test_tick_sends_only_when_still_limited_and_records(monkeypatch):
    """送る直前に画面を読み直し、まだ上限のときだけ「続けてください」を送って回数・時刻を残す。"""
    from app.commander import consoles
    from app.terminals import manager as manager_module

    fake = FakeManager(LIMIT_SCREEN)
    monkeypatch.setattr(manager_module, "manager", fake)
    monkeypatch.setattr(consoles, "list_consoles", lambda: [_row()])
    monkeypatch.setattr(resume, "plan", lambda row, now: {"action": "send", "due": now})
    audits = []
    monkeypatch.setattr(resume, "_audit", lambda sid, row, count: audits.append((sid, count)))
    assert resume.tick(now=5000.0) == ["aa000001"]
    assert fake.sent == [("aa000001", "続けてください", True)]
    assert fake.tags["aa000001"]["resume_count"] == "1" and fake.tags["aa000001"]["resume_last"] == "5000"
    assert audits == [("aa000001", 1)]
    # 利用者が先に続けていた（画面がもう作業中）なら送らない
    fake2 = FakeManager("· Thinking… (3s)\n  esc to interrupt\n")
    monkeypatch.setattr(manager_module, "manager", fake2)
    assert resume.tick(now=5000.0) == [] and fake2.sent == []


def test_tick_clears_and_schedules(monkeypatch):
    """止まっていないコンソールは印を消し、待ちのコンソールは次の予定を残す（画面に出す）。"""
    from app.commander import consoles
    from app.terminals import manager as manager_module

    fake = FakeManager("")
    monkeypatch.setattr(manager_module, "manager", fake)
    monkeypatch.setattr(consoles, "list_consoles", lambda: [_row(id="aa000002"), _row(id="aa000003")])
    plans = {"aa000002": {"action": "clear", "due": None}, "aa000003": {"action": "wait", "due": 7777.4}}
    monkeypatch.setattr(resume, "plan", lambda row, now: plans[row["id"]])
    resume.tick(now=1.0)
    assert fake.cleared == [("aa000002", ("resume_at", "resume_count", "resume_last"))]
    assert fake.tags == {"aa000003": {"resume_at": "7777"}}


def test_autoresume_toggle_api(admin_client, monkeypatch):
    """コンソールごとに自動再開を止める・戻す（off の印を付ける・外す）。無いコンソールは 404。"""
    from app.commander import consoles
    from app.terminals import manager as manager_module
    from tests.conftest import CSRF_HEADERS

    fake = FakeManager("")
    monkeypatch.setattr(manager_module, "manager", fake)
    monkeypatch.setattr(consoles, "list_consoles", lambda: [_row()])
    r = admin_client.put("/api/v1/commander/consoles/aa000001/autoresume", headers=CSRF_HEADERS,
                         json={"enabled": False})
    assert r.status_code == 200 and fake.tags["aa000001"] == {"autoresume": "off"}
    admin_client.put("/api/v1/commander/consoles/aa000001/autoresume", headers=CSRF_HEADERS, json={"enabled": True})
    assert fake.cleared == [("aa000001", ("autoresume",))]
    assert admin_client.put("/api/v1/commander/consoles/ffffffff/autoresume", headers=CSRF_HEADERS,
                            json={"enabled": False}).status_code == 404


def test_resume_message_console_then_global_then_default(admin_client, monkeypatch):
    """送る文言: コンソールの設定 → 全体の設定 → 既定「続けてください」。空で上の段へ戻す。

    根拠: 2026-11-27 利用者「継続の文言は個別設定とグローバル設定として変えられるように。既定はグローバルで」。
    """
    import stat as stat_mod

    from app.commander import consoles, settings as commander_settings
    from app.config import data_dir
    from app.terminals import manager as manager_module
    from tests.conftest import CSRF_HEADERS

    assert admin_client.get("/api/v1/commander/settings").json()["resume_message"] == "続けてください"
    r = admin_client.put("/api/v1/commander/settings", headers=CSRF_HEADERS,
                         json={"resume_message": "上限が戻ったので、続きをお願いします"})
    assert r.status_code == 200 and r.json()["resume_message"] == "上限が戻ったので、続きをお願いします"
    assert stat_mod.S_IMODE((data_dir() / "ai-commander" / "settings.json").stat().st_mode) == 0o600
    assert commander_settings.message_for(_row()) == "上限が戻ったので、続きをお願いします"
    assert commander_settings.message_for(_row(commander_resume_message="go on")) == "go on"
    # 改行は断る（Enter として届くと途中で送られる）
    assert admin_client.put("/api/v1/commander/settings", headers=CSRF_HEADERS,
                            json={"resume_message": "a\nb"}).status_code == 400

    fake = FakeManager("")
    monkeypatch.setattr(manager_module, "manager", fake)
    monkeypatch.setattr(consoles, "list_consoles", lambda: [_row()])
    assert admin_client.put("/api/v1/commander/consoles/aa000001/resume-message", headers=CSRF_HEADERS,
                            json={"message": "このコンソールだけ"}).status_code == 200
    assert fake.tags["aa000001"] == {"resume_message": "このコンソールだけ"}
    admin_client.put("/api/v1/commander/consoles/aa000001/resume-message", headers=CSRF_HEADERS, json={"message": ""})
    assert fake.cleared == [("aa000001", ("resume_message",))]
    # 全体を空にすると既定へ
    admin_client.put("/api/v1/commander/settings", headers=CSRF_HEADERS, json={"resume_message": ""})
    assert commander_settings.resume_message() == "続けてください"
