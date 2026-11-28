"""AI チームの品質の改善（2026-11-28 品質の評価の A1〜A3・S2）の試験。

目的:
- A1 review の担い手の本文に、守りの点検表（パス・リンク・秘密・権限・外部・実行）が必ず付く。
- S2 読み取りの担い手には「必要な所だけ読む」を、書く担い手には「コミットは Nexarch がする」を付ける。
- A2 finish（done）で、チーム全体の確認が合格していなければ知らせる（止めはしない）。
- A3 組み込みの既定の役割は GLM → 無料（HY4）→ ローカルの順で、hard（チームリーダーの引き継ぎ先）にも入る。
根拠: 評価 `~/ControlDeck-評価/2026-11-28-AIチームのコードの品質と改善の案.md`。AI チームのコードでパス・リンクの
守りの見落としが 2 回あり、チームの中のレビューは見つけなかった。調べもの 1 本が 17〜33 分。Codex の上限で
引き継ぎ先の無い全チームが止まった。利用者「担い手は GLM、無料モデル、ローカル LLM の順」。
既存の試験（t3a〜t3c）は本文の決まりと既定の役割の順を見ていない（docs/test-catalog.md で確認）。
"""
from __future__ import annotations

from app.commander import accounts, leader_mcp, team_roles, team_runner


def test_review_gets_the_security_checklist_and_readonly_reads_narrowly():
    review = team_runner.task_notes(True, "review")
    assert "【守りの点検表】" in review and "シンボリックリンク" in review and "【調べ方】" in review
    explore = team_runner.task_notes(True, "explore")
    assert "【守りの点検表】" not in explore and "必要な所だけ" in explore
    implement = team_runner.task_notes(False, "implement")
    assert "【コミット】" in implement and "【読み取りだけ】" not in implement


def test_finish_notes_when_team_checks_did_not_pass(monkeypatch):
    team = {"id": "tq", "tasks": [], "timeline": [], "last_team_checks": {"status": "failed"}}
    monkeypatch.setattr(leader_mcp, "_mutate", lambda team_id, fn: fn(team))
    out = leader_mcp.tool_finish("tq", {"summary": "済み", "outcome": "done"})
    assert out["team_checks"] == "failed" and "合格していません" in out["note"]
    team["last_team_checks"] = {"status": "passed"}
    out = leader_mcp.tool_finish("tq", {"summary": "済み", "outcome": "done"})
    assert out["team_checks"] == "passed" and "note" not in out


def test_builtin_roles_follow_glm_free_local(monkeypatch):
    from app.integrations.opencode import zen

    monkeypatch.setattr(accounts, "is_installed", lambda cli: True)
    monkeypatch.setattr(zen, "configured", lambda: True)
    roles = {r["name"]: r for r in team_roles.suggested_roles()}
    for name in ("explore", "implement", "hard"):
        chain = [roles[name]["primary"], *roles[name]["secondary"]]
        assert [(e["cli"], e["model"]) for e in chain] == [
            ("opencode", "opencode-go/glm-5.3"), ("opencode", "opencode/hy4-flash-free"), ("opencode", "")], name
    assert roles["review"]["primary"]["cli"] == "codex"
    monkeypatch.setattr(zen, "configured", lambda: False)   # Zen の鍵が無ければローカルだけ
    roles = {r["name"]: r for r in team_roles.suggested_roles()}
    assert roles["implement"]["primary"]["model"] == "" and roles["implement"]["secondary"] == []
