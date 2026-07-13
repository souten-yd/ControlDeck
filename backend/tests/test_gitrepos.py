"""GitHub 管理サービスのユニットテスト（ネットワーク不要分）。"""
import subprocess
from pathlib import Path

import pytest

from app.gitrepos import service as gitsvc


def test_validate_rejects_bad_url():
    with pytest.raises(gitsvc.GitError):
        gitsvc.validate("file:///etc/passwd", "ok-name")
    with pytest.raises(gitsvc.GitError):
        gitsvc.validate("https://github.com/a/b.git; rm -rf /", "ok-name")


def test_validate_rejects_bad_name():
    for bad in ["../evil", ".hidden", "a b", "x" * 65, ""]:
        with pytest.raises(gitsvc.GitError):
            gitsvc.validate("https://github.com/a/b.git", bad)


def test_validate_accepts_typical():
    gitsvc.validate("https://github.com/owner/repo.git", "repo")
    gitsvc.validate("git@github.com:owner/repo.git", "repo-2.x_y")


def test_name_from_url():
    assert gitsvc.name_from_url("https://github.com/o/repo.git") == "repo"
    assert gitsvc.name_from_url("https://github.com/o/repo") == "repo"
    assert gitsvc.name_from_url("git@github.com:o/r.git") == "r.git".removesuffix(".git")


def test_repo_path_rejects_outside(tmp_path, monkeypatch):
    monkeypatch.setattr(gitsvc, "repos_dir", lambda: tmp_path / "repos")
    (tmp_path / "repos").mkdir()
    with pytest.raises(gitsvc.GitError):
        gitsvc._repo_path(str(tmp_path / "outside"))


def test_status_and_save_and_revert(tmp_path, monkeypatch):
    root = tmp_path / "repos"
    root.mkdir()
    monkeypatch.setattr(gitsvc, "repos_dir", lambda: root)
    repo = root / "demo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "t@example.com"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=repo, check=True)
    Path(repo / "a.txt").write_text("v1")
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "v1"], cwd=repo, check=True)

    st = gitsvc.status(str(repo))
    assert st["ok"] and st["branch"] == "main" and st["dirty"] is False

    # 変更 → dirty → 保存 → リバート
    Path(repo / "a.txt").write_text("v2")
    assert gitsvc.status(str(repo))["dirty"] is True
    gitsvc.save(str(repo), "v2")
    log = gitsvc.log(str(repo))
    assert len(log) == 2 and log[0]["message"] == "v2"
    gitsvc.revert(str(repo), log[1]["sha"])
    assert Path(repo / "a.txt").read_text() == "v1"

    # 保存: 変更なしでもエラーにならない
    assert "保存済み" in gitsvc.save(str(repo), "noop")
