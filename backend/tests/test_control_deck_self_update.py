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
    monkeypatch.setattr(self_update, '_history_path', lambda: tmp_path / 'history.json')
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
    assert [h['phase'] for h in self_update.history()] == ['failed'], '失敗した更新が履歴に残っていない'



def test_finished_updates_are_kept_in_history(monkeypatch, tmp_path):
    """終わった更新を、自動か手動か・Release 名つきで新しい順に残す（設定画面と、自動更新の後の知らせが読む）。

    根拠: 2026-11-20 利用者「自動更新が動いていない気がする」。実際は動いていたが、通知先が無く画面にも
    前回の更新が出ないので気づけなかった。"""
    monkeypatch.setattr(self_update, '_state_path', lambda: tmp_path / 'update.json')
    monkeypatch.setattr(self_update, '_history_path', lambda: tmp_path / 'history.json')
    # 履歴を残し始める前の版から上がった直後は、状態ファイルの 1 回だけ見せる
    self_update._write_state({'phase': 'succeeded', 'automatic': False, 'release_tag': 'ControlDeck_v0.95',
                              'old_sha': 'a' * 40, 'new_sha': 'b' * 40, 'finished_at': 100})
    assert [h['release_tag'] for h in self_update.history()] == ['ControlDeck_v0.95']
    for tag, automatic, at in (('ControlDeck_v0.96', True, 200), ('ControlDeck_v0.97', False, 300)):
        self_update._record_history({'phase': 'running', 'automatic': automatic, 'release_tag': tag})
        self_update._record_history({'phase': 'succeeded', 'automatic': automatic, 'release_tag': tag,
                                     'old_sha': 'a' * 40, 'new_sha': 'c' * 40, 'finished_at': at})
    items = self_update.history()
    assert [(h['release_tag'], h['automatic']) for h in items] == [('ControlDeck_v0.97', False), ('ControlDeck_v0.96', True)]
    for i in range(40):
        self_update._record_history({'phase': 'failed', 'error': 'x', 'finished_at': 400 + i})
    assert len(self_update.history()) == self_update.HISTORY_LIMIT
