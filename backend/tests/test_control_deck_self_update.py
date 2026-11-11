"""No network or repository changes from read-only self-update preflight."""
from pathlib import Path

import pytest

from app.maintenance import self_update


def test_self_update_refuses_non_hex_commit():
    with pytest.raises(self_update.SelfUpdateError):
        self_update.start("../../etc/passwd")


def test_preflight_rejects_non_official_origin(monkeypatch, tmp_path):
    (tmp_path / ".git").mkdir()
    monkeypatch.setattr(self_update, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(self_update.os, "geteuid", lambda: 1000)
    monkeypatch.setattr(self_update, "_git", lambda *args, **kwargs: "file:///tmp/untrusted")
    with pytest.raises(self_update.SelfUpdateError, match="公式"):
        self_update._preflight()


def test_preflight_rejects_dirty_main(monkeypatch, tmp_path):
    (tmp_path / ".git").mkdir()
    monkeypatch.setattr(self_update, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(self_update.os, "geteuid", lambda: 1000)

    def fake_git(*args, **kwargs):
        if args[:3] == ("remote", "get-url", "origin"):
            return "https://github.com/souten-yd/ControlDeck.git"
        if args[:2] == ("branch", "--show-current"):
            return "main"
        if args[:2] == ("status", "--porcelain"):
            return " M backend/app/main.py"
        return ""

    monkeypatch.setattr(self_update, "_git", fake_git)
    with pytest.raises(self_update.SelfUpdateError, match="未コミット"):
        self_update._preflight()


def test_remote_main_rejects_non_sha(monkeypatch):
    monkeypatch.setattr(self_update, "_git", lambda *args, **kw: "refs/heads/main")
    with pytest.raises(self_update.SelfUpdateError):
        self_update._remote_main()
