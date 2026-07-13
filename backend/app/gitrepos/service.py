"""GitHub 管理: リポジトリのクローン・更新・保存・リバート。

- すべて配列引数の subprocess（shell=False）
- 認証プロンプトで固まらないよう GIT_TERMINAL_PROMPT=0（gh の credential helper は有効）
- クローン先は config.git_apps_dir 配下に限定（パストラバーサル防止）
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

from app.config import get_config

NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
URL_RE = re.compile(r"^(https?://|git@|ssh://)[\w.@:/~+-]+$")


class GitError(Exception):
    pass


def repos_dir() -> Path:
    d = Path(get_config().git_apps_dir).expanduser()
    d.mkdir(parents=True, exist_ok=True)
    return d


def name_from_url(url: str) -> str:
    base = url.rstrip("/").rsplit("/", 1)[-1]
    return base.removesuffix(".git") or "repo"


def validate(url: str, name: str) -> None:
    if not URL_RE.match(url):
        raise GitError("リポジトリ URL の形式が不正です（https:// / git@ / ssh:// のみ）")
    if not NAME_RE.match(name):
        raise GitError("名前は英数字・ピリオド・ハイフン・アンダースコアのみ（64 文字まで）")


def _env() -> dict[str, str]:
    env = dict(os.environ)
    env["GIT_TERMINAL_PROMPT"] = "0"  # 認証プロンプトで固まらず即エラーにする
    return env


def run_git(args: list[str], cwd: Path | None = None, timeout: int = 60) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args], cwd=cwd, env=_env(),
        capture_output=True, text=True, timeout=timeout,
    )


def _repo_path(path: str) -> Path:
    """DB に保存されたパスを検証して返す（git_apps_dir 配下のみ許可）。"""
    p = Path(path).resolve()
    root = repos_dir().resolve()
    if not (p == root or root in p.parents):
        raise GitError("リポジトリの場所が管理ディレクトリ外です")
    if not (p / ".git").exists():
        raise GitError(f"Git リポジトリが見つかりません: {p}")
    return p


def clone(url: str, name: str) -> Path:
    validate(url, name)
    dest = repos_dir() / name
    if dest.exists():
        raise GitError(f"既に存在します: {dest}")
    r = run_git(["clone", "--recurse-submodules", url, str(dest)], timeout=600)
    if r.returncode != 0:
        err = (r.stderr or "").strip()[-500:]
        if "Authentication" in err or "could not read Username" in err or "403" in err:
            raise GitError(
                "認証に失敗しました。非公開リポジトリは「GitHub にログイン」からログインしてください。 " + err
            )
        raise GitError(f"クローンに失敗しました: {err}")
    return dest


def status(path: str) -> dict:
    try:
        p = _repo_path(path)
    except GitError as e:
        return {"ok": False, "error": str(e)}
    branch = run_git(["rev-parse", "--abbrev-ref", "HEAD"], cwd=p).stdout.strip()
    last = run_git(["log", "-1", "--format=%h%x09%ct%x09%s"], cwd=p).stdout.strip()
    sha, ts, msg = (last.split("\t", 2) + ["", ""])[:3] if last else ("", "", "")
    dirty = bool(run_git(["status", "--porcelain"], cwd=p).stdout.strip())
    return {
        "ok": True,
        "branch": branch,
        "commit": sha,
        "commit_time": int(ts) if ts.isdigit() else None,
        "commit_message": msg,
        "dirty": dirty,
    }


def log(path: str, n: int = 20) -> list[dict]:
    p = _repo_path(path)
    r = run_git(["log", f"-{n}", "--format=%H%x09%ct%x09%s"], cwd=p)
    entries = []
    for line in r.stdout.splitlines():
        sha, ts, msg = (line.split("\t", 2) + ["", ""])[:3]
        entries.append({"sha": sha, "time": int(ts) if ts.isdigit() else None, "message": msg})
    return entries


def update(path: str) -> str:
    p = _repo_path(path)
    r = run_git(["pull", "--ff-only", "--recurse-submodules"], cwd=p, timeout=300)
    if r.returncode != 0:
        err = (r.stderr or r.stdout).strip()[-500:]
        if "diverged" in err or "Not possible to fast-forward" in err:
            raise GitError("ローカルに独自の変更があり fast-forward できません。先に保存/リバートしてください。" + err)
        raise GitError(f"更新に失敗しました: {err}")
    return r.stdout.strip()[-500:] or "最新の状態です"


def save(path: str, message: str) -> str:
    p = _repo_path(path)
    run_git(["add", "-A"], cwd=p)
    r = run_git(["commit", "-m", message], cwd=p)
    if r.returncode != 0:
        out = (r.stdout + r.stderr).strip()
        if "nothing to commit" in out:
            return "変更はありません（保存済み）"
        raise GitError(f"保存に失敗しました: {out[-500:]}")
    return r.stdout.strip()[-300:]


def revert(path: str, sha: str) -> str:
    p = _repo_path(path)
    if not re.match(r"^[0-9a-f]{7,40}$", sha):
        raise GitError("コミット ID が不正です")
    # 対象がこのリポジトリの履歴に存在することを確認
    if run_git(["cat-file", "-t", sha], cwd=p).stdout.strip() != "commit":
        raise GitError("指定のコミットが見つかりません")
    r = run_git(["reset", "--hard", sha], cwd=p)
    if r.returncode != 0:
        raise GitError(f"リバートに失敗しました: {(r.stderr or '').strip()[-500:]}")
    return r.stdout.strip()[-300:]


def remove_files(path: str) -> None:
    p = _repo_path(path)
    if p == repos_dir().resolve():
        raise GitError("管理ディレクトリ自体は削除できません")
    shutil.rmtree(p)


def gh_auth_status() -> dict:
    gh = shutil.which("gh")
    if gh is None:
        return {"available": False, "logged_in": False, "account": ""}
    r = subprocess.run([gh, "auth", "status"], capture_output=True, text=True, timeout=15)
    out = r.stdout + r.stderr
    m = re.search(r"account (\S+)", out)
    return {"available": True, "logged_in": r.returncode == 0, "account": m.group(1) if m else ""}
