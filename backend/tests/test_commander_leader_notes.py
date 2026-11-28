"""目的: 走っているチームリーダーへ、利用者・Claude から伝言（指示・レビューの指摘）を送れること。チームリーダーは
team_status・wait の new_notes で受け取り、読むまでは finish できない。

根拠: 2026-11-29、NewsHub のチームの途中のレビューで要修正を見つけたが、走っているチームリーダーへ伝える口が無く、
「チームの前提」に書くしかなかった（利用者「AI チームの開発の課題を解決しながら改修して」）。
"""
from __future__ import annotations

import pytest

from app.commander import leader_mcp


@pytest.fixture
def team(monkeypatch):
    data = {"id": "t1", "name": "t", "timeline": [], "tasks": [], "leader_run": {"status": "running", "goal": "g"}}
    monkeypatch.setattr(leader_mcp, "_mutate", lambda team_id, fn: fn(data))
    monkeypatch.setattr(leader_mcp, "_read", lambda team_id: data)
    return data


def test_note_is_delivered_once_and_blocks_finish_until_read(team):
    note = leader_mcp.add_note("t1", "旧行は主張と 1:1 にして", "Claude")
    assert note["id"] == 1 and any("Claudeからチームリーダーへ" in r["text"] for r in team["timeline"])
    with pytest.raises(leader_mcp.ToolError) as exc:
        leader_mcp.tool_finish("t1", {"summary": "済み", "outcome": "done"})
    assert exc.value.status == 409
    assert [n["text"] for n in leader_mcp._take_notes("t1")] == ["旧行は主張と 1:1 にして"]  # noqa: SLF001
    assert leader_mcp._take_notes("t1") == []                                                # noqa: SLF001
    assert not leader_mcp._has_unread(team)                                                  # noqa: SLF001


def test_empty_note_is_refused(team):
    with pytest.raises(leader_mcp.ToolError):
        leader_mcp.add_note("t1", "   ", "利用者")
