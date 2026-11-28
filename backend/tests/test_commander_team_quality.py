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


def test_no_handoff_tasks_return_to_todo_after_the_limit_recovers(monkeypatch):
    """上限で「引き継ぎ先なし」になった仕事は、止まった担い手の回復の時刻が過ぎたら待ちへ戻る。
    上限に当たっていない担い手を理由には戻さない（行き来しない）。2026-11-28 本番: レビュー 2 件が回復後も止まったまま。"""
    from app.commander import team_dispatch, teams

    codex = {"kind": "headless", "cli": "codex", "account": "default", "model": "", "effort": "high"}
    role = {"id": "rrev", "name": "review", "count": 1, "primary": codex, "secondary": [], "last_index": 0}
    team = {"id": "tz", "roles": [role], "limits": {team_roles.endpoint_key(codex): {"until": 1000.0, "at": 0.0}},
            "tasks": [{"id": 7, "status": "blocked", "blocked_reason": "no_handoff", "role": "rrev", "assignee": "x", "events": []}]}
    saved = {}
    monkeypatch.setattr(teams, "_load", lambda: [team])
    monkeypatch.setattr(teams, "_save", lambda all_teams: saved.setdefault("n", 0) or saved.update(n=1))
    assert team_dispatch.requeue_recovered("tz", 999.0) == 0 and team["tasks"][0]["status"] == "blocked"   # まだ回復前
    assert team_dispatch.requeue_recovered("tz", 1001.0) == 1
    assert team["tasks"][0]["status"] == "todo" and "blocked_reason" not in team["tasks"][0] and "assignee" not in team["tasks"][0]
    team["tasks"][0].update(status="blocked", blocked_reason="no_handoff")
    team["limits"] = {}                                                                                   # 上限の記録が無い
    assert team_dispatch.requeue_recovered("tz", 2000.0) == 0
