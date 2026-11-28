"""Nexarch の AI チームの git の作業場所（T3c・コード・LLM なし）。

- チームの作業場所が git ならチームのブランチ `nexarch/<team>`、implement の仕事ごとに worktree＋ブランチ `nexarch/<team>-<n>` を作る
  （git は `nexarch/<team>` と `nexarch/<team>/<n>` を同時に持てない（ref のディレクトリが衝突する）ので `-` でつなぐ）。
  worktree は作業場所の中の `.nexarch-wt/` に置く（`.git` の外・`.git/info/exclude` へ足すので未管理に見えない・
  作業フォルダの許可の中に収まる）。`.worktreeinclude`（1 行 1 パターンの glob）があれば、未管理のファイルを worktree へ写す。
- git でなければ、チームの設定 `git_init`（既定 true）なら最初に `git init` してから同じ形にする。false なら使わない（enabled=False。
  担当ファイルの重なりで待つ従来の形）。作業場所が git の入れ子の中（toplevel でない）のときも使わない。
- 統合（merge）はチームのブランチの worktree で `git merge --no-ff`。競合したら競合のファイルを返して abort する（元のブランチは触らない）。
- 「取り込む」（land）は元から git だった作業場所だけ: 元のブランチへチームのブランチを merge する（作業場所が汚れていれば断る）。
  Nexarch が `git init` した作業場所は、終わりに元のブランチへ fast-forward で反映する（元の状態が無いので）。
- shell=True は使わない。環境は最小限・コミットの名前は Nexarch・フックは動かさない（core.hooksPath=/dev/null）・端末の入力を待たない。
"""
from __future__ import annotations

import fnmatch
import logging
import os
import re
import shutil
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)

WT_DIR = ".nexarch-wt"
TIMEOUT = 120
DIFF_MAX = 30000
COPY_MAX_BYTES = 5 * 1024 * 1024
COPY_MAX_FILES = 200
_IDENT = {"GIT_AUTHOR_NAME": "Nexarch", "GIT_AUTHOR_EMAIL": "nexarch@localhost",
          "GIT_COMMITTER_NAME": "Nexarch", "GIT_COMMITTER_EMAIL": "nexarch@localhost"}
_BRANCH_OK = re.compile(r"^[A-Za-z0-9._/-]{1,120}$")


class GitError(Exception):
    pass


def _env() -> dict[str, str]:
    env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "GIT_TERMINAL_PROMPT": "0", "LC_ALL": "C", **_IDENT}
    for key in ("HOME", "TMPDIR"):
        if os.environ.get(key):
            env[key] = os.environ[key]
    return env


def run(cwd: Path | str, *args: str, timeout: int = TIMEOUT) -> tuple[int, str]:
    """git を 1 回（shell なし）。(終了コード, 標準出力＋標準エラー)。git が無い・時間切れは (127, 理由)。"""
    try:
        done = subprocess.run(  # noqa: S603 - shell=False・引数は固定の語と検証済みのブランチ名・パスだけ
            ["git", "-c", "core.hooksPath=/dev/null", "-c", "commit.gpgsign=false", "-c", "core.quotepath=off", *args],
            cwd=str(cwd), capture_output=True, text=True, timeout=timeout, env=_env(), check=False, stdin=subprocess.DEVNULL)
    except subprocess.TimeoutExpired:
        return 124, "git が時間切れです"
    except OSError as exc:
        return 127, f"git を実行できません: {exc.strerror or 'OSError'}"
    return done.returncode, (done.stdout + done.stderr)


def _ok(cwd: Path | str, *args: str) -> str:
    code, out = run(cwd, *args)
    if code != 0:
        raise GitError(f"git {args[0]} に失敗しました: {out.strip()[:300]}")
    return out


def branch_name(team_id: str, n: int | None = None) -> str:
    name = f"nexarch/{team_id}" + (f"-{n}" if n is not None else "")
    if not _BRANCH_OK.fullmatch(name):
        raise GitError("ブランチ名が正しくありません")
    return name


def wt_root(workdir: Path | str) -> Path:
    return Path(workdir) / WT_DIR


def _toplevel(path: Path) -> Path | None:
    code, out = run(path, "rev-parse", "--show-toplevel")
    if code != 0:
        return None
    try:
        return Path(out.strip()).resolve()
    except OSError:
        return None


def _exclude(path: Path) -> None:
    code, out = run(path, "rev-parse", "--git-path", "info/exclude")
    if code != 0:
        return
    target = Path(out.strip())
    target = target if target.is_absolute() else path / target
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        existing = target.read_text(encoding="utf-8") if target.exists() else ""
        lines = existing.splitlines()
        add = [line for line in (WT_DIR + "/", ".nexarch/") if line not in lines]
        if add:
            target.write_text(existing + ("" if existing.endswith("\n") or not existing else "\n")
                             + "".join(line + "\n" for line in add), encoding="utf-8")
    except OSError:
        logger.warning("worktree の除外を書けませんでした")


def ensure_repo(workdir: str, team_id: str, *, git_init: bool = True) -> dict:
    """チームの git の状態（作る）。冪等。返す dict: enabled・origin_was_git・base_branch・team_branch・team_wt・reason。"""
    path = Path(workdir).resolve()
    info = {"enabled": False, "origin_was_git": False, "base_branch": "", "team_branch": "", "team_wt": "", "reason": ""}
    top = _toplevel(path)
    if top is not None and top != path:
        info["reason"] = "作業場所が別の git リポジトリの中にあります（入れ子には作りません）"
        return info
    if top is None:
        if not git_init:
            info["reason"] = "git ではなく、git_init が false です"
            return info
        _ok(path, "init", "-q", "-b", "main")
        info["origin_was_git"] = False
    else:
        info["origin_was_git"] = True
    code, _ = run(path, "rev-parse", "--verify", "HEAD")
    if code != 0:  # コミットが 1 つも無い
        if not info["origin_was_git"]:
            _ok(path, "add", "-A")
        _ok(path, "commit", "-q", "--allow-empty", "-m", "Nexarch: 初期状態")
    elif not info["origin_was_git"]:
        pass
    _exclude(path)
    branch = _ok(path, "rev-parse", "--abbrev-ref", "HEAD").strip()
    if branch == "HEAD":
        info["reason"] = "HEAD が分離しています（ブランチへ戻してください）"
        return info
    info["base_branch"] = branch
    team_branch = branch_name(team_id)
    info["team_branch"] = team_branch
    team_wt = wt_root(path) / "team"
    if not team_wt.is_dir():
        code, _ = run(path, "rev-parse", "--verify", team_branch)
        if code == 0:
            _ok(path, "worktree", "add", str(team_wt), team_branch)
        else:
            _ok(path, "worktree", "add", "-b", team_branch, str(team_wt), "HEAD")
    info["team_wt"] = str(team_wt)
    info["enabled"] = True
    return info


def _copy_includes(src: Path, dest: Path) -> int:
    """`.worktreeinclude` の glob に合う、git が管理していないファイルを worktree へ写す（上限つき・外へ出るパスは使わない）。"""
    listing = src / ".worktreeinclude"
    try:
        patterns = [ln.strip() for ln in listing.read_text(encoding="utf-8").splitlines() if ln.strip() and not ln.startswith("#")]
    except OSError:
        return 0
    copied = 0
    code, out = run(src, "ls-files", "--others", "-i", "--exclude-standard", "-z")
    untracked = [p for p in out.split("\0") if p] if code == 0 else []
    code, out = run(src, "ls-files", "--others", "--exclude-standard", "-z")
    untracked += [p for p in out.split("\0") if p] if code == 0 else []
    for rel in dict.fromkeys(untracked):
        if rel.startswith(WT_DIR + "/") or rel.startswith("/") or ".." in Path(rel).parts:
            continue
        if not any(fnmatch.fnmatch(rel, pat) or fnmatch.fnmatch(Path(rel).name, pat) for pat in patterns):
            continue
        source = src / rel
        try:
            if source.is_symlink() or not source.is_file() or source.stat().st_size > COPY_MAX_BYTES:
                continue
            target = dest / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
        except OSError:
            continue
        copied += 1
        if copied >= COPY_MAX_FILES:
            break
    return copied


def create_item_worktree(workdir: str, info: dict, team_id: str, n: int) -> tuple[str, str]:
    """implement の仕事 n の worktree とブランチ（チームのブランチの今の先頭から）。(パス, ブランチ)。あれば使い回す。

    worktree へ .nexarch（依頼全文・資料）と node_modules・.venv のリンクを渡す（brief E・2026-11-28。
    今まではチームリーダーが手で写していた）。"""
    from app.commander import team_inputs

    path = Path(workdir).resolve()
    branch = branch_name(team_id, n)
    wt = wt_root(path) / str(n)
    if not wt.is_dir():
        code, _ = run(path, "rev-parse", "--verify", branch)
        if code == 0:
            _ok(path, "worktree", "add", str(wt), branch)
        else:
            _ok(path, "worktree", "add", "-b", branch, str(wt), info["team_branch"])
        _copy_includes(path, wt)
    try:
        team_inputs.share_worktree(str(path), str(wt))
    except OSError as exc:
        logger.warning("worktree へ .nexarch を渡せませんでした: %s", exc)
    return str(wt), branch


def commit_all(wt: Path | str, message: str) -> bool:
    """worktree の変更をコミットする。コミットした（変更があった）か。"""
    code, out = run(wt, "status", "--porcelain")
    if code != 0 or not out.strip():
        return False
    _ok(wt, "add", "-A")
    code, _ = run(wt, "commit", "-q", "-m", message[:200])
    return code == 0


def merge_into_team(info: dict, branch: str, message: str) -> dict:
    """item のブランチをチームのブランチへ merge。{"merged": bool, "conflicts": [ファイル]}。競合なら abort する。"""
    team_wt = info["team_wt"]
    code, out = run(team_wt, "merge", "--no-ff", "-m", message[:200], branch)
    if code == 0:
        return {"merged": True, "conflicts": []}
    code2, files = run(team_wt, "diff", "--name-only", "--diff-filter=U")
    conflicts = [f for f in files.splitlines() if f.strip()][:50]
    run(team_wt, "merge", "--abort")
    if not conflicts:
        raise GitError(f"merge に失敗しました: {out.strip()[:300]}")
    return {"merged": False, "conflicts": conflicts}


def start_conflict_merge(wt: Path | str, team_branch: str) -> list[str]:
    """implement の worktree へチームのブランチを取り込む（競合は印を残したまま。担い手が直す）。競合のファイルを返す。"""
    code, _ = run(wt, "merge", "--no-ff", "-m", "Nexarch: チームのブランチを取り込み", team_branch)
    if code == 0:
        return []
    _, files = run(wt, "diff", "--name-only", "--diff-filter=U")
    return [f for f in files.splitlines() if f.strip()][:50]


def diff(info: dict, branch: str, limit: int = DIFF_MAX) -> dict:
    """チームのブランチからの差分（stat と本体。大きさの上限つき）。"""
    team_wt = info["team_wt"]
    spec = f"{info['team_branch']}...{branch}"
    code, stat = run(team_wt, "diff", "--stat", spec)
    code2, body = run(team_wt, "diff", spec)
    if code != 0 or code2 != 0:
        return {"stat": "", "diff": "", "truncated": False}
    return {"stat": stat.strip()[-2000:], "diff": body[:limit], "truncated": len(body) > limit}


def numstat(info: dict, branch: str) -> dict:
    """変更ファイルと行数（追加・削除）。"""
    code, out = run(info["team_wt"], "diff", "--numstat", f"{info['team_branch']}...{branch}")
    files: list[str] = []
    added = removed = 0
    if code == 0:
        for line in out.splitlines():
            parts = line.split("\t")
            if len(parts) == 3:
                files.append(parts[2])
                added += int(parts[0]) if parts[0].isdigit() else 0
                removed += int(parts[1]) if parts[1].isdigit() else 0
    return {"files": files[:100], "added": added, "removed": removed}


def team_numstat(info: dict) -> dict:
    """チームのブランチ全体（元のブランチからの）の変更ファイルと行数。"""
    code, out = run(info["team_wt"], "diff", "--numstat", f"{info['base_branch']}...{info['team_branch']}")
    files: list[str] = []
    added = removed = 0
    if code == 0:
        for line in out.splitlines():
            parts = line.split("\t")
            if len(parts) == 3:
                files.append(parts[2])
                added += int(parts[0]) if parts[0].isdigit() else 0
                removed += int(parts[1]) if parts[1].isdigit() else 0
    return {"files": files[:200], "count": len(files), "added": added, "removed": removed}


def land(workdir: str, info: dict) -> dict:
    """チームのブランチを元のブランチへ取り込む（元から git の作業場所だけ）。{"ok": bool, "message": str}。"""
    path = Path(workdir).resolve()
    if not info.get("enabled"):
        return {"ok": False, "message": "git の作業場所ではありません"}
    code, branch = run(path, "rev-parse", "--abbrev-ref", "HEAD")
    if code != 0 or branch.strip() != info["base_branch"]:
        return {"ok": False, "message": f"作業場所が元のブランチ（{info['base_branch']}）にありません"}
    code, dirty = run(path, "status", "--porcelain")
    if dirty.strip():
        return {"ok": False, "message": "作業場所に未コミットの変更があります（コミットか退避をしてから取り込んでください）"}
    code, out = run(path, "merge", "--no-ff", "-m", f"Nexarch: {info['team_branch']} を取り込み", info["team_branch"])
    if code != 0:
        run(path, "merge", "--abort")
        return {"ok": False, "message": f"取り込めませんでした（競合の可能性）: {out.strip()[:200]}"}
    return {"ok": True, "message": "取り込みました"}


def fast_forward_base(workdir: str, info: dict) -> bool:
    """Nexarch が git init した作業場所の終わり: 元のブランチをチームのブランチへ進める（fast-forward だけ）。"""
    code, _ = run(workdir, "merge", "--ff-only", info["team_branch"])
    return code == 0


def cleanup(workdir: str, team_id: str) -> None:
    """チームを消すとき: worktree とブランチを片付ける（失敗しても止めない）。"""
    path = Path(workdir)
    root = wt_root(path)
    if not root.is_dir():
        return
    for wt in sorted(root.iterdir()):
        run(path, "worktree", "remove", "--force", str(wt))
    shutil.rmtree(root, ignore_errors=True)
    run(path, "worktree", "prune")
    code, out = run(path, "branch", "--list", f"nexarch/{team_id}*")
    for line in out.splitlines():
        name = line.strip().lstrip("*+ ").strip()
        if name and _BRANCH_OK.fullmatch(name):
            run(path, "branch", "-D", name)
