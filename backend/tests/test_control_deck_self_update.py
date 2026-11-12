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
    import io
    monkeypatch.setattr(self_update.urllib.request, "urlopen", lambda *a, **kw: io.BytesIO(b'{"tag_name":"ControlDeck_v0.11"}'))
    monkeypatch.setattr(self_update, "_git", lambda *args, **kw: "refs/heads/main")
    with pytest.raises(self_update.SelfUpdateError):
        self_update._remote_main()


def test_release_uses_peeled_tag_commit(monkeypatch):
    import io
    monkeypatch.setattr(self_update.urllib.request, "urlopen", lambda *a, **kw:
        io.BytesIO(b'{"tag_name":"ControlDeck_v0.12","draft":false,"prerelease":false}'))
    monkeypatch.setattr(self_update, "_git", lambda *a, **kw:
        "a" * 40 + "\trefs/tags/ControlDeck_v0.12\n" + "b" * 40 + "\trefs/tags/ControlDeck_v0.12^{}")
    assert self_update._remote_release() == ("ControlDeck_v0.12", "b" * 40)


@pytest.mark.parametrize("body", ['{"tag_name":"main"}',
    '{"tag_name":"ControlDeck_v0.12","prerelease":true}',
    '{"tag_name":"../../bad"}'])
def test_release_rejects_untrusted_tag_or_prerelease(monkeypatch, body):
    import io
    monkeypatch.setattr(self_update.urllib.request, "urlopen", lambda *a, **kw: io.BytesIO(body.encode()))
    with pytest.raises(self_update.SelfUpdateError):
        self_update._remote_release()
