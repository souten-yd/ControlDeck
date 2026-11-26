"""AI Commander の状態の判定（C2・LLM なし）の試験。

目的: コンソールの画面の文字から「作業中・入力待ち・上限で停止・エラー・終了」と最後の行を正しく出す。
自動再開（C3）はこの判定の「上限で停止」を見て動くので、誤判定は再開の空振り・止め損ねになる。
根拠: 2026-11-27 に実機で取った画面（Claude Code 2.1.332 の作業中、Codex 0.172.0・OpenCode v2.4.2 の
入力待ち）を縮めて使う。上限の文言は C0 の実機の記録（c1-spec.md）。
既存の Terminal の試験（test_terminals.py）は CLI の状態を扱わないので同じ観点は無い（test-catalog で確認）。
"""
from __future__ import annotations

from app.commander import consoles, status

# 実機の Claude Code の作業中の画面（2026-11-27、縮めた）
CLAUDE_WORKING = """\
● I'll check whether there are real CLI screens
  available as samples.

  Bash(tmux capture-pane -p -t cdterm-bcc78a3e -S -40)
  ⎿  Running…

· Drizzling… (20s · ↓ 1.5k tokens)
  ⎿  Tip: Use /permissions to pre-approve and
     pre-deny bash, edit, and MCP tools

────────────────────────────────────────────────────
❯ 
────────────────────────────────────────────────────
  ⏵⏵ auto mode on · 1 shell, 1 monitor · esc to interrupt
"""

CLAUDE_WAITING = """\
● 直しました。テストは 18 件とも通りました。

────────────────────────────────────────────────────
❯ 
────────────────────────────────────────────────────
  ⏵⏵ auto mode on · ? for shortcuts
"""

CLAUDE_LIMIT = """\
● 続きを書きます。
  ⎿  You've hit your 5-hour limit · resets 10:20pm (Asia/Tokyo)

────────────────────────────────────────────────────
❯ 
────────────────────────────────────────────────────
  ? for shortcuts
"""

# 実機の Codex の入力待ち（2026-11-27）
CODEX_WAITING = """\
  >_ OpenAI Codex (v0.172.0)
     /data1tb/ControlDeck/CodeDEV
  To get started, describe a task or try one of these commands:
  /review - review any changes and find issues
› Ask Codex to do anything
  GPT-6.1-Sol low · /data1tb/ControlDeck/CodeDEV
  ← for agents · ? for shortcuts
"""

CODEX_LIMIT = """\
■ You've hit your usage limit. Upgrade to Pro or try again at Nov 28th, 2026 3:05 AM.
› Ask Codex to do anything
  ← for agents · ? for shortcuts
"""

CODEX_WORKING = """\
• Working (12s • esc to interrupt)
› Ask Codex to do anything
"""

# 実機の OpenCode v2.4.2 の入力待ち（2026-11-27）
OPENCODE_WAITING = """\
                       ┃
                       ┃  Ask anything… "Fix broken tests"
                       ┃
                       ┃  Build · Big Pickle OpenCode
                       ╹▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀
                       /data1tb/ControlDeck/CodeDEV              shift+tab agents  ctrl+p commands
"""


def test_states_from_real_screens():
    """実機の画面で 5 つの状態を正しく出す。"""
    assert status.classify("claude", CLAUDE_WORKING, program="claude")["state"] == status.WORKING
    assert status.classify("claude", CLAUDE_WAITING, program="claude")["state"] == status.WAITING
    limited = status.classify("claude", CLAUDE_LIMIT, program="claude")
    assert limited["state"] == status.LIMITED
    assert "resets 10:20pm" in limited["detail"]
    assert status.classify("codex", CODEX_WAITING, program="codex")["state"] == status.WAITING
    assert status.classify("codex", CODEX_WORKING, program="codex")["state"] == status.WORKING
    codex_limit = status.classify("codex", CODEX_LIMIT, program="codex")
    assert codex_limit["state"] == status.LIMITED and "try again at" in codex_limit["detail"].lower()
    assert status.classify("opencode", OPENCODE_WAITING, program="podman")["state"] == status.WAITING
    # 終了: pane が死んでいる・CLI が終わってシェルだけ
    assert status.classify("claude", CLAUDE_WAITING, alive=False)["state"] == status.EXITED
    assert status.classify("codex", CODEX_WAITING, program="bash")["state"] == status.EXITED


def test_error_only_near_the_bottom_and_not_from_conversation_text():
    """エラーは画面の一番下の数行だけで判定する（会話の本文に出た「API Error」の話は拾わない）。"""
    talk = "● API Error の直し方を説明します。\n" + "\n".join(f"  行 {i}" for i in range(10)) + "\n❯ \n  ? for shortcuts\n"
    assert status.classify("claude", talk, program="claude")["state"] == status.WAITING
    failed = "● 続けます。\n  ⎿  API Error: 500 Internal Server Error\n❯ \n  ? for shortcuts\n"
    judged = status.classify("claude", failed, program="claude")
    assert judged["state"] == status.ERROR and "500" in judged["detail"]


def test_limit_wording_does_not_trigger_on_ordinary_numbers_and_working_wins():
    """「429 tokens」などの本文では上限にしない。上限の後に再開して作業中なら作業中。"""
    text = "● 429 tokens を使いました。resets the counter.\n❯ \n  ? for shortcuts\n"
    assert status.classify("claude", text, program="claude")["state"] == status.WAITING
    resumed = CLAUDE_LIMIT + "\n● 再開します。\n· Thinking… (3s)\n  esc to interrupt\n"
    assert status.classify("claude", resumed, program="claude")["state"] == status.WORKING


def test_last_line_skips_frames_prompts_and_hints():
    """最後の行は枠・空の入力欄・操作の案内を除いた、意味のある最後の行。"""
    assert status.last_line("claude", CLAUDE_WAITING) == "● 直しました。テストは 18 件とも通りました。"
    assert status.last_line("codex", CODEX_WAITING) == "GPT-6.1-Sol low · /data1tb/ControlDeck/CodeDEV"
    assert status.last_line("claude", CLAUDE_WORKING).startswith("· Drizzling")
    assert len(status.last_line("claude", "x" * 500)) == status.LAST_LINE_MAX
    assert status.last_line("claude", "────\n❯ \n") == ""


def test_console_list_carries_state_and_last_line(monkeypatch):
    """コンソールの一覧に状態・最後の行・上限の行が付く（画面は tmux の capture-pane）。"""
    class Fake:
        def list_sessions(self):
            return [
                {"id": "aa000001", "alive": True, "program": "claude",
                 "commander_cli": "claude", "commander_account": "default"},
                {"id": "aa000002", "alive": False, "program": "codex",
                 "commander_cli": "codex", "commander_account": "default"},
            ]

        def capture_text(self, sid, lines=60):
            assert lines == status.CAPTURE_LINES
            return {"aa000001": CLAUDE_LIMIT}.get(sid, "")

    monkeypatch.setattr(consoles, "manager", Fake())
    rows = {row["id"]: row for row in consoles.list_consoles()}
    assert rows["aa000001"]["commander_state"] == status.LIMITED
    assert "resets" in rows["aa000001"]["commander_detail"]
    assert rows["aa000001"]["commander_last_line"].startswith("⎿  You've hit") or rows["aa000001"]["commander_last_line"]
    assert rows["aa000002"]["commander_state"] == status.EXITED


# 実機（2026-11-27）: 幅が狭く「esc to i…」に切れ、利用者が次の指示を入れて待たせている画面
CLAUDE_WORKING_NARROW_QUEUED = """\
● Bash(tmux capture-pane -p -J -t cdterm-bcc78a3e -S -30)
  ⎿  Running…
❯ Ai コマンダーにてコンソールを開く際、作業フォルダ
  を選ぶが、新規でもフォルダを作成できるようにして
  ⎿  Queued · ctrl+x ctrl+s to send now
· Drizzling… (2m 39s · thought for 1s)
  ⎿  Tip: Use /permissions to pre-approve and
     pre-deny bash, edit, and MCP tools
──────────────────────────────────────────────────
❯ Press up to edit queued messages
──────────────────────────────────────────────────
  ⏵⏵ auto mode on · 1 shell, 1 monitor · esc to i…
"""


def test_narrow_screen_with_queued_message_is_still_working():
    """幅が狭くて中断の案内が切れても、作業中の行（記号＋動詞…＋経過時間）で作業中と判定する。

    根拠: 2026-11-27 の実機で「入力待ち」と誤判定した画面。最後の行に待たせた指示の案内を出さない。
    """
    judged = status.classify("claude", CLAUDE_WORKING_NARROW_QUEUED, program="claude")
    assert judged["state"] == status.WORKING
    assert judged["last_line"].startswith("· Drizzling… (2m 39s")
    spinner_only = "· Thinking… (12s · ↓ 3k tokens)\n❯ \n"
    assert status.classify("claude", spinner_only, program="claude")["state"] == status.WORKING
