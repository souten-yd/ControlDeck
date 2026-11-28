"""目的: Project Lab から、プロジェクト（CodeDEV の中のフォルダ）が Nexarch の AI チームの作業場所なら、チームの状態・
仕事・チームのブランチの変更ファイルと差分を読めること。差分は変更ファイルの一覧にあるものだけ。

根拠: 2026-11-28 利用者「チームの作業場所を設定したと思うんだけど、プロジェクトラボでその成果物とか見れないんでしょうか。
見れるようにしてほしい」。チームの成果は作業場所の中の別のブランチ（nexarch/<チーム>）にあり、Project Lab の
ファイルの一覧（元のブランチ）には出ていなかった。
"""
from __future__ import annotations

import subprocess

import pytest

from app.project_lab import team_view


def _git(cwd, *args):
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", *args], cwd=cwd, check=True, capture_output=True)


@pytest.fixture
def team_repo(tmp_path, monkeypatch):
    repo = tmp_path / "NewsHub"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    (repo / "a.py").write_text("x = 1\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", "base")
    _git(repo, "checkout", "-q", "-b", "nexarch/t1")
    (repo / "a.py").write_text("x = 2\ny = 3\n")
    (repo / "b.py").write_text("z = 1\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", "team")
    _git(repo, "checkout", "-q", "main")
    team = {"id": "t1", "name": "測定の記録", "workdir": str(repo),
            "git": {"enabled": True, "base_branch": "main", "team_branch": "nexarch/t1"},
            "leader_run": {"status": "running", "goal": "C2 と C3"}, "result": {},
            "tasks": [{"id": 1, "title": "表を作る", "status": "done", "tier": "implement", "model_label": "HY4"},
                      {"id": 2, "title": "やめた", "status": "dropped"}]}
    from app.commander import teams

    monkeypatch.setattr(teams, "list_teams", lambda: [team])
    return repo


def test_summary_lists_changed_files_and_tasks(team_repo):
    out = team_view.team_summary(team_repo)
    assert out["name"] == "測定の記録" and out["team_branch"] == "nexarch/t1"
    assert {f["path"] for f in out["files"]} == {"a.py", "b.py"}
    assert out["added"] == 3 and out["removed"] == 1
    assert [t["id"] for t in out["tasks"]] == [1]                  # 取り下げた仕事は出さない
    assert team_view.team_summary(team_repo.parent) is None       # チームの作業場所でなければ無い


def test_file_diff_only_for_changed_files(team_repo):
    out = team_view.team_file_diff(team_repo, "a.py")
    assert "+x = 2" in out["diff"] and not out["truncated"]
    for bad in ("../etc/passwd", "--output=/tmp/x", "c.py"):
        with pytest.raises(KeyError):
            team_view.team_file_diff(team_repo, bad)
