"""AI チームの実行・確認のコマンドの「出力の上限」の試験。

目的: 2026-11-28 に本番で、チームリーダーの Codex が始めて 1 秒で落ちた（終了コード -25 = SIGXFSZ）。
子のプロセスに RLIMIT_FSIZE（50MB）を掛けていたので、Codex が自分の記録（~/.codex/logs_2.sqlite 66MB）を
書いた瞬間に落ちた。上限は「出力のファイル」だけに効かせる。次を守る:
- 子が出力以外の大きなファイルを書いても落ちない（実行・確認のコマンドの両方）。
- 出力が上限を超えたら止め、失敗の理由に「出力が…MB を超えた」と出る。
- シグナルで落ちたときは、理由にシグナルの名前が出る（-25 だけでは分からない）。
根拠: 本番の失敗の記録（leader_run.reason「実行が落ちました（終了コード -25）」）。
既存の試験（t2b・t3a）は小さな出力しか扱わず、上限は見ていない（docs/test-catalog.md で確認）。
"""
from __future__ import annotations

import sys
from pathlib import Path

from app.commander import team_checks, team_runner

BIG = 3 * 1024 * 1024   # 試験では上限を 1MB に下げ、それより大きいファイルを書かせる


def test_check_command_may_write_big_files_but_not_big_output(monkeypatch, tmp_path):
    monkeypatch.setattr(team_checks, "OUTPUT_FILE_MAX", 1024 * 1024)
    monkeypatch.setattr(team_runner, "OUTPUT_FILE_MAX", 1024 * 1024)
    writer = tmp_path / "w.py"
    writer.write_text(f"open('big.bin','wb').write(b'0'*{BIG}); print('ok')\n")
    done = team_checks._run_one(f"{sys.executable} {writer}", tmp_path)
    assert done["exit_code"] == 0, done
    assert (tmp_path / "big.bin").stat().st_size == BIG
    talker = tmp_path / "t.py"
    talker.write_text("import sys,time\nwhile True:\n    sys.stdout.write('x'*65536); sys.stdout.flush()\n")
    done = team_checks._run_one(f"{sys.executable} {talker}", tmp_path)
    assert done["exit_code"] != 0 and "MB を超えたので止めました" in done["tail"], done


def test_run_is_not_killed_for_writing_big_files(monkeypatch, tmp_path):
    """チームの実行（_execute）で、CLI が自分の記録のような大きなファイルを書いても落ちない。"""
    monkeypatch.setattr(team_runner, "OUTPUT_FILE_MAX", 1024 * 1024)
    monkeypatch.setattr(team_runner, "POLL_INTERVAL", 0.05)
    script = tmp_path / "cli.py"
    script.write_text(f"open('state.sqlite','wb').write(b'0'*{BIG}); print('{{\"type\":\"result\",\"result\":\"done\"}}')\n")
    monkeypatch.setattr(team_runner, "build_launch",
                        lambda member, text, cwd, last, **kw: ([sys.executable, str(script)], {"PATH": "/usr/bin:/bin"}, lambda: None))
    result = team_runner._execute(("t", 1), {"cli": "claude"}, "x", tmp_path)
    assert result["exit"] == 0 and not result["too_large"], result
    assert (tmp_path / "state.sqlite").stat().st_size == BIG


def test_run_stops_when_output_is_too_large(monkeypatch, tmp_path):
    monkeypatch.setattr(team_runner, "OUTPUT_FILE_MAX", 1024 * 1024)
    monkeypatch.setattr(team_runner, "POLL_INTERVAL", 0.05)
    script = tmp_path / "cli.py"
    script.write_text("import sys\nwhile True:\n    sys.stdout.write('x'*65536); sys.stdout.flush()\n")
    monkeypatch.setattr(team_runner, "build_launch",
                        lambda member, text, cwd, last, **kw: ([sys.executable, str(script)], {"PATH": "/usr/bin:/bin"}, lambda: None))
    result = team_runner._execute(("t", 2), {"cli": "claude"}, "x", tmp_path, timeout=30)
    assert result["too_large"] is True
    assert "MB を超えたので止めました" in team_runner.failure_reason(result)


def test_failure_reason_names_the_signal():
    assert team_runner.failure_reason({"exit": -25}) == "終了コード -25（シグナル SIGXFSZ）"
    assert team_runner.failure_reason({"exit": 2}) == "終了コード 2"


def test_no_rlimit_on_children():
    """子のプロセスに RLIMIT_FSIZE を掛けない（ソースに残っていないこと）。"""
    for module in (team_runner, team_checks):
        assert "setrlimit" not in Path(module.__file__).read_text(encoding="utf-8")


def test_leader_goal_accepts_a_real_spec_length():
    """依頼の上限は API の入口（20000 字）と同じ。2026-11-28 本番で 5638 字の仕様が「4000 文字以内」で断られた。"""
    from app.commander import router, team_leader

    assert team_leader.GOAL_MAX == router._AutoTeam.model_fields["goal"].metadata[0].max_length == 20000
