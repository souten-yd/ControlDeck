"""Nexarch の 5 時間・週の使用率と回復の時刻（C4 の一部）の試験。

目的: Claude は statusline（Claude Code が渡す rate_limits）、Codex は sessions の記録の rate_limits から、
アカウントごとに使用率と回復の時刻を出す。ログインの鍵は読まない。回復を過ぎた窓は 0 と見なす。
根拠: 2026-11-27 利用者「5 時間制限と週間制限の利用率とリセットまでの時間を追加して」。形は実機
（Claude Code 2.1.332 の statusline の説明・Codex 0.172.0 の rollout）。既存の試験に同じ観点は無い。
"""
from __future__ import annotations

import json
import stat
import subprocess
import sys
import time

from app.commander import accounts, usage


def test_statusline_script_writes_only_limits_and_prints_summary(tmp_path):
    """statusline のコマンド: 使用率と回復の時刻と model だけを 0600 で書き、短い 1 行を出す。"""
    out = tmp_path / "claude-default.json"
    payload = {"model": {"id": "claude-opus-5-5", "display_name": "Opus 5.5"},
               "workspace": {"current_dir": "/secret/path"}, "session_id": "s1",
               "rate_limits": {"five_hour": {"used_percentage": 12.4, "resets_at": 1795700000},
                               "seven_day": {"used_percentage": 40, "resets_at": 1796000000}}}
    done = subprocess.run([sys.executable, "-I", str(usage.STATUSLINE_SCRIPT), str(out)],
                          input=json.dumps(payload), capture_output=True, text=True, timeout=10)
    assert done.stdout.strip() == "5h 12% · 7d 40%"
    saved = json.loads(out.read_text())
    assert set(saved) == {"five_hour", "seven_day", "model", "updated_at"}
    assert saved["five_hour"] == {"used_percent": 12.4, "resets_at": 1795700000.0}
    assert "/secret/path" not in out.read_text()
    assert stat.S_IMODE(out.stat().st_mode) == 0o600
    # rate_limits が無い（API キーの利用など）ときは書かない
    out.unlink()
    subprocess.run([sys.executable, "-I", str(usage.STATUSLINE_SCRIPT), str(out)],
                   input=json.dumps({"model": {"id": "x"}}), capture_output=True, text=True, timeout=10)
    assert not out.exists()


def test_claude_settings_file_points_the_statusline_at_the_account_file():
    """--settings のファイルは statusline だけで、書き出し先はアカウントごと。"""
    path = usage.claude_settings_file("default")
    data = json.loads(path.read_text())
    assert set(data) == {"statusLine"}
    assert str(usage.claude_usage_file("default")) in data["statusLine"]["command"]
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_claude_usage_reads_and_expires_windows():
    now = time.time()
    usage.claude_usage_file("default").write_text(json.dumps({
        "five_hour": {"used_percent": 80, "resets_at": now + 3600},
        "seven_day": {"used_percent": 30, "resets_at": now - 10},   # もう回復した
        "updated_at": now}))
    got = usage.usage_for("claude", "default")
    assert got["five_hour"] == {"used_percent": 80.0, "resets_at": now + 3600}
    assert got["seven_day"] == {"used_percent": 0.0, "resets_at": None}


def test_codex_usage_from_session_records(monkeypatch):
    """Codex は sessions の最新の rollout の最後の rate_limits（primary=5 時間・secondary=週）。"""
    accounts.create_account(accounts.CLIS["codex"], "usage-probe")
    home = accounts.account_dir(accounts.CLIS["codex"], "usage-probe")
    day = home / "sessions" / "2026" / "11" / "27"
    day.mkdir(parents=True)
    now = time.time()
    lines = [
        {"type": "event_msg", "payload": {"type": "token_count", "rate_limits": {
            "primary": {"used_percent": 1.0, "window_minutes": 300, "resets_at": now + 100}}}},
        {"type": "event_msg", "payload": {"type": "token_count", "rate_limits": {
            "limit_id": "codex", "primary": {"used_percent": 55.0, "window_minutes": 300, "resets_at": now + 7200},
            "secondary": {"used_percent": 27.0, "window_minutes": 10080, "resets_at": now + 86400}}}},
    ]
    (day / "rollout-2026-11-27T07-00-00-x.jsonl").write_text("\n".join(json.dumps(x) for x in lines) + "\n")
    got = usage.usage_for("codex", "usage-probe")
    assert got["five_hour"]["used_percent"] == 55.0 and got["seven_day"]["used_percent"] == 27.0
    assert got["source"] == "codex-sessions"
    assert usage.usage_for("opencode", "default") is None
    accounts.delete_account(accounts.CLIS["codex"], "usage-probe")
