"""Nexarch の Claude・Codex は API キーを使わない（定額のログインだけ）の試験。

目的: 利用者の決定（2026-11-28「クロードとコーデックスは API キーを消してください。基本的にはもう定額でしか
使えません」）を仕組みで守る。コンソール（tmux）とチーム（画面のない実行）の両方で、
- API キーの環境変数（ANTHROPIC_API_KEY・OPENAI_API_KEY など）を CLI へ渡さないこと、
- Claude は --settings で apiKeyHelper を空・forceLoginMethod=claudeai にすること、
- Codex は forced_login_method="chatgpt" を付けること、
を確かめる。
根拠: 2026-11-28 の実機。Claude は forceLoginMethod を付けても ANTHROPIC_API_KEY を使う
（"Invalid API key"）ので、環境変数を消すことが要。apiKeyHelper は --settings で空にすると使われない。
Codex は forced_login_method で API キーのログイン・環境変数の鍵を断る。
既存の試験（test_commander_consoles / team_t3a）は argv の形を見るだけで、鍵の除外は見ていない
（docs/test-catalog.md で確認）。
"""
from __future__ import annotations

import json
import os

import pytest

from app.commander import accounts, consoles, team_runner

KEYS = {"ANTHROPIC_API_KEY": "sk-ant-x", "ANTHROPIC_AUTH_TOKEN": "t", "OPENAI_API_KEY": "sk-x", "CODEX_API_KEY": "sk-y"}


@pytest.fixture
def with_keys(monkeypatch, tmp_path):
    for key, value in KEYS.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setattr(accounts, "executable_path", lambda cli: f"/opt/bin/{cli.id}")
    monkeypatch.setattr(team_runner, "codex_sandbox_ok", lambda: True)
    return tmp_path


@pytest.mark.parametrize("cli_id", ["claude", "codex"])
def test_team_launch_drops_api_key_env(with_keys, cli_id):
    """チームの実行の環境に、サービスの環境にある API キーが入らない。"""
    argv, env, _ = team_runner.build_launch({"cli": cli_id, "account": "default"}, "x", with_keys, with_keys / "last")
    assert not set(KEYS) & set(env)
    assert all("sk-" not in part for part in argv)


def test_team_claude_forces_subscription_login(with_keys):
    argv, _, _ = team_runner.build_launch({"cli": "claude", "account": "default"}, "x", with_keys, with_keys / "last")
    settings = json.loads(argv[argv.index("--settings") + 1])
    assert settings == {"apiKeyHelper": "", "forceLoginMethod": "claudeai"}


def test_team_codex_forces_chatgpt_login(with_keys):
    argv, _, _ = team_runner.build_launch({"cli": "codex", "account": "default"}, "本文", with_keys, with_keys / "last")
    at = argv.index('forced_login_method="chatgpt"')
    assert argv[at - 1] == "-c" and argv[1] == "exec" and argv[-1] == "本文"


@pytest.mark.parametrize("cli_id", ["claude", "codex"])
def test_console_command_unsets_api_keys(with_keys, cli_id):
    """コンソールは tmux のサーバーの環境を引き継ぐので、コマンドの頭で鍵を消す。"""
    command, env = consoles._command_for(accounts.CLIS[cli_id], "/tmp", 1)
    assert command.startswith(accounts.UNSET_API_KEYS + " ")
    for key in KEYS:
        assert f"-u {key}" in command
    assert not set(KEYS) & set(env or {})
    if cli_id == "codex":
        assert "forced_login_method=\"chatgpt\"" in command


def test_codex_login_status_does_not_see_env_keys(monkeypatch, with_keys):
    """`codex login status` に環境変数の鍵を渡さない（鍵を「ログイン」と取り違えない）。"""
    seen = {}

    def fake_run(argv, **kw):
        seen.update(kw["env"])

        class Done:
            stdout, stderr = "Logged in using ChatGPT\n", ""
        return Done()

    import subprocess
    monkeypatch.setattr(subprocess, "run", fake_run)
    assert accounts._codex_login("default") == "ChatGPT でログイン"
    assert not set(KEYS) & set(seen)
    assert os.environ["OPENAI_API_KEY"] == "sk-x"   # サービス自身の環境は変えない
