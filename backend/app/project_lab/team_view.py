"""Project Lab から Nexarch の AI チームの成果物を見る（読むだけ）。

2026-11-28 利用者「チームの作業場所って設定したと思うんだけど、プロジェクトラボでその成果物とか見れないんでしょうか。
今後チームを使うことがあると思うので見れるようにしてほしい」。

プロジェクト（CodeDEV の中のフォルダ）が、あるチームの作業場所なら、そのチームの状態・仕事・チームのブランチの
変更ファイルと差分を返す。チームの成果は作業場所の中の別のブランチ（nexarch/<チーム>）にあるので、プロジェクトの
ファイルの一覧（元のブランチ）には出ない。

守り: git は読むだけ（diff・numstat）・shell なし（team_git.run）。差分を出すファイルは numstat の一覧にあるものだけ
（利用者の入力のパスをそのまま git に渡さない）。差分は大きさの上限つき。
"""
from __future__ import annotations

import os
from pathlib import Path

DIFF_MAX = 256 * 1024
FILES_MAX = 300


def _team_of(path: Path) -> dict | None:
    from app.commander import teams

    real = os.path.realpath(path)
    for team in teams.list_teams():
        workdir = str(team.get("workdir") or "")
        if workdir and os.path.realpath(workdir) == real:
            return team
    return None


def _numstat(workdir: str, base: str, branch: str) -> list[dict]:
    from app.commander import team_git

    code, out = team_git.run(workdir, "diff", "--numstat", f"{base}...{branch}")
    rows: list[dict] = []
    if code != 0:
        return rows
    for line in out.splitlines():
        parts = line.split("\t")
        if len(parts) == 3:
            rows.append({"path": parts[2], "added": int(parts[0]) if parts[0].isdigit() else None,
                         "removed": int(parts[1]) if parts[1].isdigit() else None})
    return rows[:FILES_MAX]


def team_summary(project_path: Path) -> dict | None:
    """このプロジェクトがチームの作業場所なら、チームの状態・仕事・変更ファイル。違えば None。"""
    team = _team_of(project_path)
    if team is None:
        return None
    git = team.get("git") or {}
    leader = team.get("leader_run") or {}
    result = team.get("result") or {}
    tasks = [{
        "id": task.get("id"), "title": str(task.get("title") or "")[:200], "status": task.get("status"),
        "tier": task.get("tier") or "", "model": task.get("model_label") or "", "branch": task.get("branch") or "",
    } for task in team.get("tasks", []) if task.get("status") != "dropped"]
    files: list[dict] = []
    if git.get("enabled") and git.get("base_branch") and git.get("team_branch"):
        files = _numstat(str(team["workdir"]), str(git["base_branch"]), str(git["team_branch"]))
    return {
        "id": team["id"], "name": team.get("name") or "", "leader_status": leader.get("status") or "",
        "goal": str(leader.get("goal") or "")[:2000], "summary": str(result.get("summary") or "")[:4000],
        "landed": bool(result.get("landed")), "base_branch": git.get("base_branch") or "",
        "team_branch": git.get("team_branch") or "", "tasks": tasks, "files": files,
        "added": sum(f["added"] or 0 for f in files), "removed": sum(f["removed"] or 0 for f in files),
    }


def team_file_diff(project_path: Path, file_path: str) -> dict:
    """チームのブランチの 1 ファイルの差分（元のブランチから）。ファイルは変更ファイルの一覧にあるものだけ。"""
    from app.commander import team_git

    team = _team_of(project_path)
    if team is None:
        raise KeyError("チームの作業場所ではありません")
    git = team.get("git") or {}
    base, branch = str(git.get("base_branch") or ""), str(git.get("team_branch") or "")
    if not (git.get("enabled") and base and branch):
        raise KeyError("チームのブランチがありません")
    known = {row["path"] for row in _numstat(str(team["workdir"]), base, branch)}
    if file_path not in known:
        raise KeyError("変更ファイルの一覧にありません")
    code, body = team_git.run(str(team["workdir"]), "diff", f"{base}...{branch}", "--", file_path)
    if code != 0:
        raise KeyError("差分を読めませんでした")
    return {"path": file_path, "diff": body[:DIFF_MAX], "truncated": len(body) > DIFF_MAX}
