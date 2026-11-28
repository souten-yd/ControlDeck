"""目的: AI チーム（Nexarch）の通知を種類に分け、利用者が設定の「通知」で入り切りできること。

根拠: 2026-11-28 利用者「AI チームで何かあった時に通知が来てほしい。どういう通知が来てもいいかを設定で変更できるようにしたい」。
それまでチームの通知は全部 1 つの種類（team_task）で、種類ごとに選べなかった。
"""
from __future__ import annotations

import asyncio

from app.alerts import events
from app.commander import team_watch


def test_team_kinds_are_listed_in_their_own_group():
    view_groups = {g for g, _ in events.GROUPS}
    assert "team" in view_groups
    kinds = {k.id: k for k in events.KINDS if k.group == "team"}
    assert set(kinds) == {"team_question", "team_finish", "team_blocked", "team_report", "team_handoff"}
    assert kinds["team_question"].default and kinds["team_finish"].default and kinds["team_blocked"].default
    assert not kinds["team_report"].default and not kinds["team_handoff"].default   # 数が多いので既定は切


def test_watch_events_map_to_notify_kinds():
    assert team_watch.notify_kind({"kind": team_watch.KIND_REPORT}) == "team_report"
    assert team_watch.notify_kind({"kind": team_watch.KIND_HANDOFF}) == "team_handoff"
    assert team_watch.notify_kind({"kind": team_watch.KIND_NO_HANDOFF}) == "team_blocked"
    assert team_watch.notify_kind({"notify_kind": "team_question"}) == "team_question"
    assert team_watch.notify_kind({}) == "team_blocked"
    for kind in team_watch.NOTIFY_KIND.values():
        assert kind in events.KIND_BY_ID


def test_send_passes_the_kind_to_the_channel(monkeypatch):
    sent = []

    async def channels():
        return [("controldeck", "")]

    async def fake_send(channel_type, url, title, message, kind="alert", urgent=False):
        sent.append((channel_type, kind))
        return True

    monkeypatch.setattr(team_watch, "_channels", channels)
    monkeypatch.setattr("app.alerts.notify.send_notification", fake_send)
    asyncio.run(team_watch.send("Nexarch", "質問しています", "team_question"))
    assert sent == [("controldeck", "team_question")]
