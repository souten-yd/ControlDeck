"""静的サイトとしてGitHub Pagesへ公開する。

ダウンロードと違い、ここは押した瞬間に中身がインターネットへ出る。取り消しても
索引や cache には残る前提で扱う。したがって:

  - 入れる file の選別は export と同じ規則を使う。公開経路にだけ緩い判定を
    置くと、緩い方から漏れる。
  - 公開前に何が出るかを必ず返す。呼び出し側はそれを見せてから実行する。
  - 認証は gh CLI が既に持っているものを借りる。Control Deck 側に新しい鍵を
    保管しない——保管しなければ漏れない。

push は毎回 force で行う。公開しているのは「いまのプロジェクトの姿」であって
歴史ではない。履歴を積むと、一度混ざった秘密情報を後から消せなくなる。
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.config import data_dir
from app.project_lab import export

# 静的サイトの入口を置く場所。上から順に見て、最初に index.html があった所を既定にする。
PUBLISH_DIR_CANDIDATES = ["dist", "build", "out", "public", "_site", "site", "docs", ""]
REPO_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$")
GIT_TIMEOUT_SECONDS = 300
GH_TIMEOUT_SECONDS = 120


class PublishError(ValueError):
    pass


@dataclass
class PublishTarget:
    project: Path
    directory: str          # project からの相対。"" は project 直下
    root: Path              # 実際に公開する directory


def _state_path() -> Path:
    root = (data_dir() / "project-lab").resolve()
    root.mkdir(parents=True, exist_ok=True)
    return root / "publish.json"


def _load_state() -> dict[str, Any]:
    try:
        raw = json.loads(_state_path().read_text(encoding="utf-8"))
        return raw if isinstance(raw, dict) else {}
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        return {}


def _save_state(project_id: str, entry: dict[str, Any]) -> None:
    state = _load_state()
    state[project_id] = entry
    path = _state_path()
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temp, path)


def get_state(project_id: str) -> dict[str, Any] | None:
    return _load_state().get(project_id)


def resolve_target(project: Path, directory: str | None) -> PublishTarget:
    """公開する directory を決める。project の外は指させない。"""
    if directory is None:
        directory = detect_directory(project)
    normalized = (directory or "").strip().replace("\\", "/").strip("/")
    if ".." in normalized.split("/") or normalized.startswith(("/", "~")) or "\x00" in normalized:
        raise PublishError("公開ディレクトリの指定が不正です")
    root = project if not normalized else (project / normalized)
    try:
        resolved = root.resolve(strict=True)
    except (FileNotFoundError, OSError) as exc:
        raise PublishError("公開ディレクトリが見つかりません") from exc
    if not resolved.is_dir():
        raise PublishError("公開ディレクトリがディレクトリではありません")
    if resolved != project.resolve() and project.resolve() not in resolved.parents:
        raise PublishError("プロジェクト外は公開できません")
    return PublishTarget(project=project, directory=normalized, root=resolved)


def detect_directory(project: Path) -> str:
    """index.html のある所を探す。無ければ project 直下を返す。"""
    for candidate in PUBLISH_DIR_CANDIDATES:
        root = project / candidate if candidate else project
        if (root / "index.html").is_file():
            return candidate
    return ""


def directory_candidates(project: Path) -> list[dict[str, Any]]:
    """画面で選ばせるための候補。index.html の有無まで返す。"""
    found: list[dict[str, Any]] = []
    for candidate in PUBLISH_DIR_CANDIDATES:
        root = project / candidate if candidate else project
        if not root.is_dir():
            continue
        found.append({
            "directory": candidate,
            "hasIndex": (root / "index.html").is_file(),
        })
    return found


def plan(target: PublishTarget) -> export.ExportPlan:
    """公開する file の選別。ダウンロードと同じ規則を使う。"""
    return export.plan(target.root)


def _run(args: list[str], cwd: Path | None = None, timeout: int = GIT_TIMEOUT_SECONDS,
         extra_env: dict[str, str] | None = None) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env["GIT_TERMINAL_PROMPT"] = "0"   # 認証を聞かれても固まらせない
    if extra_env:
        env.update(extra_env)
    try:
        return subprocess.run(args, cwd=cwd, env=env, capture_output=True,
                              text=True, timeout=timeout)
    except FileNotFoundError as exc:
        raise PublishError(f"{args[0]} が見つかりません") from exc
    except subprocess.TimeoutExpired as exc:
        raise PublishError(f"{args[0]} が時間内に終わりませんでした") from exc


def _fail(step: str, result: subprocess.CompletedProcess) -> PublishError:
    # 失敗の中身にはURLやリポジトリ名が出る。鍵は出ないが、長いので頭だけ返す。
    detail = (result.stderr or result.stdout or "").strip().splitlines()
    return PublishError(f"{step}に失敗しました: {detail[0] if detail else '原因不明'}")


def gh_account() -> dict[str, Any]:
    """gh の認証状態。公開の可否をこれで判断する。"""
    if shutil.which("gh") is None:
        return {"available": False, "loggedIn": False, "account": ""}
    result = _run(["gh", "auth", "status"], timeout=GH_TIMEOUT_SECONDS)
    text = f"{result.stdout}{result.stderr}"
    match = re.search(r"account (\S+)", text)
    return {
        "available": True,
        "loggedIn": result.returncode == 0,
        "account": match.group(1) if match else "",
    }


def _git_credential_env() -> dict[str, str]:
    """push の認証を gh に肩代わりさせる。

    remote URL へ token を埋めると、.git/config にも log にも残る。global の
    git config も書き換えたくないので、この呼び出しの間だけ credential helper を
    渡す。token は git と gh の間だけを通り、こちらの手には入らない。
    """
    return {
        "GIT_CONFIG_COUNT": "1",
        "GIT_CONFIG_KEY_0": "credential.https://github.com.helper",
        "GIT_CONFIG_VALUE_0": "!gh auth git-credential",
    }


def _stage(target: PublishTarget, export_plan: export.ExportPlan, workdir: Path) -> None:
    """選別済みの file だけを作業ディレクトリへ写す。"""
    for path in export_plan.files:
        relative = path.relative_to(target.root)
        destination = workdir / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, destination)
    # これが無いと GitHub Pages は Jekyll を通す。`_` で始まる directory が
    # 丸ごと無視され、bundler の出力が404になる。
    (workdir / ".nojekyll").write_text("", encoding="utf-8")


def _ensure_repository(name: str, visibility: str, account: str) -> str:
    """repository を用意して owner/name を返す。既にあればそれを使う。"""
    full = f"{account}/{name}" if "/" not in name else name
    exists = _run(["gh", "repo", "view", full, "--json", "name"], timeout=GH_TIMEOUT_SECONDS)
    if exists.returncode == 0:
        return full
    created = _run(
        ["gh", "repo", "create", full, f"--{visibility}",
         "--description", "Published from Control Deck Project Lab"],
        timeout=GH_TIMEOUT_SECONDS,
    )
    if created.returncode != 0:
        raise _fail("リポジトリの作成", created)
    return full


def _enable_pages(full_name: str, branch: str) -> str:
    """Pages を有効にして公開URLを返す。既に有効なら設定を更新する。"""
    created = _run(
        ["gh", "api", "-X", "POST", f"repos/{full_name}/pages",
         "-f", f"source[branch]={branch}", "-f", "source[path]=/"],
        timeout=GH_TIMEOUT_SECONDS,
    )
    if created.returncode != 0:
        # 409 は「もう有効」。その場合だけ更新に切り替える。他は素直に失敗させる。
        if "409" not in f"{created.stdout}{created.stderr}":
            raise _fail("GitHub Pages の有効化", created)
        updated = _run(
            ["gh", "api", "-X", "PUT", f"repos/{full_name}/pages",
             "-f", f"source[branch]={branch}", "-f", "source[path]=/"],
            timeout=GH_TIMEOUT_SECONDS,
        )
        if updated.returncode != 0:
            raise _fail("GitHub Pages の設定更新", updated)
    info = _run(["gh", "api", f"repos/{full_name}/pages", "--jq", ".html_url"],
                timeout=GH_TIMEOUT_SECONDS)
    url = info.stdout.strip()
    return url or f"https://github.com/{full_name}"


def _clear_state(project_id: str) -> None:
    state = _load_state()
    if state.pop(project_id, None) is None:
        return
    path = _state_path()
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temp, path)


def unpublish(project_id: str) -> dict[str, Any]:
    """公開を取り下げる。

    リポジトリ自体は消さない。gh の token に delete_repo が無いのが直接の理由だが、
    仮にあっても既定では消さない方がよい——公開を止めたいだけの操作で、履歴も
    issue も一緒に消えるのは取り返しがつかない。

    代わりに Pages を無効にし、公開していた branch を消す。これで URL は 404 に
    なり、中身も残らない。同じ場所へ公開し直すこともできる。
    """
    entry = get_state(project_id)
    if entry is None:
        raise PublishError("このプロジェクトは公開されていません")
    account = gh_account()
    if not account["available"]:
        raise PublishError("gh CLI が見つかりません")
    if not account["loggedIn"]:
        raise PublishError("gh が GitHub にログインしていません")

    full_name = str(entry.get("repository") or "")
    branch = str(entry.get("branch") or "gh-pages")
    removed: list[str] = []

    pages = _run(["gh", "api", "-X", "DELETE", f"repos/{full_name}/pages"],
                 timeout=GH_TIMEOUT_SECONDS)
    text = f"{pages.stdout}{pages.stderr}"
    if pages.returncode == 0:
        removed.append("pages")
    elif "404" not in text:
        # 既に無効なら 404。それ以外は黙って成功にしない。
        raise _fail("GitHub Pages の無効化", pages)

    ref = _run(["gh", "api", "-X", "DELETE",
                f"repos/{full_name}/git/refs/heads/{branch}"],
               timeout=GH_TIMEOUT_SECONDS)
    ref_text = f"{ref.stdout}{ref.stderr}"
    if ref.returncode == 0:
        removed.append("branch")
    elif "404" not in ref_text and "422" not in ref_text:
        raise _fail("公開ブランチの削除", ref)

    _clear_state(project_id)
    return {
        "repository": full_name,
        "branch": branch,
        "removed": removed,
        # リポジトリは残る。何が残っているかを黙らない。
        "repositoryRemains": True,
        "repositoryUrl": f"https://github.com/{full_name}",
    }


def publish(project_id: str, project: Path, *, directory: str | None,
            repository: str, visibility: str, branch: str = "gh-pages") -> dict[str, Any]:
    """公開する。戻り値はそのまま画面と監査ログへ渡せる形にする。"""
    if visibility not in {"public", "private"}:
        raise PublishError("visibility は public か private です")
    if not REPO_NAME_RE.match(repository.split("/")[-1]):
        raise PublishError("リポジトリ名は英数字・ピリオド・ハイフン・アンダースコアのみです")
    account = gh_account()
    if not account["available"]:
        raise PublishError("gh CLI が見つかりません。GitHub Pages への公開には gh が必要です")
    if not account["loggedIn"]:
        raise PublishError("gh が GitHub にログインしていません。`gh auth login` を実行してください")

    target = resolve_target(project, directory)
    export_plan = plan(target)
    if not export_plan.files:
        raise PublishError("公開できる file がありません")
    if not (target.root / "index.html").is_file():
        raise PublishError("index.html がありません。公開ディレクトリを確認してください")

    full_name = _ensure_repository(repository, visibility, account["account"])
    with tempfile.TemporaryDirectory(prefix="cd-publish-") as temp:
        workdir = Path(temp)
        _stage(target, export_plan, workdir)
        steps = [
            (["git", "init", "-q", "-b", branch], "リポジトリの初期化"),
            (["git", "config", "user.name", "Control Deck"], "コミット情報の設定"),
            (["git", "config", "user.email", "control-deck@localhost"], "コミット情報の設定"),
            (["git", "add", "-A"], "ファイルの追加"),
            (["git", "commit", "-q", "-m", f"Publish {project_id} from Control Deck"], "コミット"),
            (["git", "remote", "add", "origin", f"https://github.com/{full_name}.git"], "リモートの設定"),
        ]
        for args, label in steps:
            result = _run(args, cwd=workdir)
            if result.returncode != 0:
                raise _fail(label, result)
        # 歴史ではなく「いまの姿」を出す。積み上げると混ざった秘密を消せなくなる。
        pushed = _run(["git", "push", "--force", "origin", branch],
                      cwd=workdir, extra_env=_git_credential_env())
        if pushed.returncode != 0:
            raise _fail("push", pushed)

    url = _enable_pages(full_name, branch)
    entry = {
        "repository": full_name,
        "visibility": visibility,
        "branch": branch,
        "directory": target.directory,
        "url": url,
        "fileCount": len(export_plan.files),
        "excludedCount": len(export_plan.excluded),
    }
    _save_state(project_id, entry)
    return entry
