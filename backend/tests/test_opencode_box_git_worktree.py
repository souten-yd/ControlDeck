"""箱の OpenCode から、git の worktree（Nexarch のチームの担い手の作業場所）で git と依頼の全文が見える、の試験。

目的: 担い手の worktree の `.git` が指す `<repo>/.git/worktrees/<名前>` と、チームの `.nexarch/` を箱につなぐ。
本体の `.git` は読み取り専用で、書けるのはその worktree の分だけ。`.git` が本体の外を指していたらつながない。
根拠: 2026-11-28 本番の AI チームで、担い手から「箱の中で brief・git・venv が見えなかった」と報告があった。
本体の `.git/config` を書けると、ホストの git の filter などからコードが走るので、読み取り専用にする。
既存の試験（test_opencode_*）は worktree を扱っていない（docs/test-catalog.md で確認）。
"""
from __future__ import annotations

import subprocess
from pathlib import Path

from app.integrations.opencode import provider


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", *args], cwd=cwd, check=True, capture_output=True)


def test_worktree_inside_repo_gets_its_git_and_brief(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "commit", "-q", "--allow-empty", "-m", "a")
    (repo / ".nexarch").mkdir()
    (repo / ".nexarch" / "brief.md").write_text("依頼")
    _git(repo, "worktree", "add", "-q", "-b", "w1", str(repo / ".nexarch-wt" / "1"))
    writable, readonly = provider._git_worktree_mounts(repo / ".nexarch-wt" / "1")
    assert writable == [(repo / ".git" / "worktrees" / "1").resolve()]
    assert readonly == [(repo / ".git").resolve(), repo / ".nexarch"]


def test_plain_repo_and_outside_pointers_get_nothing(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    assert provider._git_worktree_mounts(repo) == ([], [])          # ふつうのリポジトリ（.git はフォルダ）
    other = tmp_path / "other"
    (other / ".git" / "worktrees" / "x").mkdir(parents=True)
    fake = tmp_path / "fake"
    fake.mkdir()
    (fake / ".git").write_text(f"gitdir: {other / '.git' / 'worktrees' / 'x'}\n")
    assert provider._git_worktree_mounts(fake) == ([], [])          # 本体の外のリポジトリを指している
    (fake / ".git").write_text("gitdir: /home\n")
    assert provider._git_worktree_mounts(fake) == ([], [])          # worktrees の形でない


def test_launch_mounts_git_readonly_and_worktree_writable(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "commit", "-q", "--allow-empty", "-m", "a")
    _git(repo, "worktree", "add", "-q", "-b", "w1", str(repo / ".nexarch-wt" / "1"))
    seen = {}
    from app.maintenance import sandbox

    monkeypatch.setattr(provider, "isolation_mode", lambda: "box")
    monkeypatch.setattr(sandbox, "container_argv", lambda command, **kw: seen.update(kw) or ["podman"])
    provider._launch("v2", ["opencode2"], repo / ".nexarch-wt" / "1", tmp_path / "cfg", interactive=False)
    assert (repo / ".git").resolve() in seen["readonly"]
    assert (repo / ".git" / "worktrees" / "1").resolve() in seen["writable"]
    assert (repo / ".git").resolve() not in seen["writable"]
