"""設定 → オプション機能の「AI の CLI（Nexarch）」（Claude Code・Codex・Gemini）の試験。

目的: 利用者の npm のグローバル（端末でも使う実体）へ導入・更新・削除する。管理領域に別の写しを作らない。
根拠: 2026-11-27 利用者「各 CLI の導入と削除と更新ができるように Settings の機能に追加して」。npm は呼ばず
（取得しない）、呼び方だけを確かめる。既存の npm の機能の試験は管理領域への導入で、同じ観点は無い。
"""
from __future__ import annotations

import subprocess

from app.features import registry


def test_cli_features_use_npm_global(monkeypatch, tmp_path):
    bindir = tmp_path / "npm-global" / "bin"
    bindir.mkdir(parents=True)
    calls = []

    def fake_npm(args):
        calls.append(args)
        exe = bindir / "claude"
        if args[0] == "install":
            exe.write_text("#!/bin/sh\necho 2.1.400\n")
            exe.chmod(0o755)
        elif args[0] == "uninstall" and exe.exists():
            exe.unlink()
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr(registry, "_npm_global_bin", lambda: bindir)
    monkeypatch.setattr(registry, "_npm_global", fake_npm)
    monkeypatch.setattr(registry.shutil, "which", lambda name: "/usr/bin/npm" if name == "npm" else None)
    assert registry.FEATURES["claude-code"]["category"] == "cli"
    assert registry.status("claude-code")["installed"] is False
    state = registry.install("claude-code")
    assert calls[-1] == ["install", "-g", "@anthropic-ai/claude-code"]
    assert state["installed"] and state["managed"] and "2.1.400" in state["version"]
    registry.update("claude-code")
    assert calls[-1] == ["install", "-g", "@anthropic-ai/claude-code@latest"]
    assert registry.uninstall("claude-code")["installed"] is False
    assert calls[-1] == ["uninstall", "-g", "@anthropic-ai/claude-code"]
    assert {registry.FEATURES[k]["package"] for k in ("claude-code", "codex-cli", "gemini-cli")} == {
        "@anthropic-ai/claude-code", "@openai/codex", "@google/gemini-cli"}
