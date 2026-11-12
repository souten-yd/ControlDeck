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


def test_failed_health_rolls_back_and_keeps_private_backup(monkeypatch, tmp_path):
    old, new = 'a' * 40, 'b' * 40
    calls, states = [], []
    monkeypatch.setattr(self_update, 'REPO_ROOT', tmp_path)
    monkeypatch.setattr(self_update, '_state_path', lambda: tmp_path / 'update.json')
    monkeypatch.setattr(self_update, '_preflight', lambda: old)
    monkeypatch.setattr(self_update, '_remote_release', lambda: ('ControlDeck_v0.12', new))
    monkeypatch.setattr(self_update, '_write_state', lambda s: states.append(s))
    monkeypatch.setattr(self_update, '_prepare', lambda: calls.append(('prepare',)))
    def git(*args, **kwargs):
        calls.append(args)
        return new if args[0] == 'rev-parse' else ''
    def command(argv, **kwargs):
        calls.append(tuple(argv))
        if 'backup' in argv:
            assert Path(argv[-1]).stat().st_mode & 0o777 == 0o700
    checks = 0
    def health():
        nonlocal checks
        checks += 1
        if checks == 1:
            raise self_update.SelfUpdateError('unhealthy')
    monkeypatch.setattr(self_update, '_git', git)
    monkeypatch.setattr(self_update, '_cmd', command)
    monkeypatch.setattr(self_update, '_health_check', health)
    with pytest.raises(self_update.SelfUpdateError, match='unhealthy'):
        self_update.apply(new)
    assert ('reset', '--hard', old) in calls
    assert checks == 2
    assert states[-1]['phase'] == 'failed'
    backup_index = next(i for i, c in enumerate(calls) if 'backup' in c)
    merge_index = calls.index(('merge', '--ff-only', new))
    assert backup_index < merge_index
