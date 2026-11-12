"""Explicit, user-scheduled ControlDeck source update (not a startup hook).

The web process only checks and launches an independent systemd user job.
The worker allows fast-forward updates of a clean checked-out main from the
fixed ControlDeck repository. A failed build restores the previous checkout.
Never run this automatically at application startup.
"""
from __future__ import annotations

import fcntl
import json
import os
import re
import subprocess
import sys
import time
import urllib.request
from pathlib import Path
from typing import Any

from app.config import REPO_ROOT, data_dir

_ORIGINS = {
    "https://github.com/souten-yd/ControlDeck",
    "https://github.com/souten-yd/ControlDeck.git",
    "git@github.com:souten-yd/ControlDeck.git",
    "ssh://git@github.com/souten-yd/ControlDeck.git",
}
_SERVICE = "control-deck-web.service"
_SHA_RE = re.compile(r"^[0-9a-f]{40}$")


class SelfUpdateError(RuntimeError):
    pass


def _state_path() -> Path:
    return data_dir().resolve() / "updates" / "control-deck.json"


def _write_state(state: dict[str, Any]) -> None:
    path = _state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)


def _saved_state() -> dict:
    try:
        value = json.loads(_state_path().read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def _cmd(argv: list[str], *, timeout: int = 30, check: bool = True, cwd: Path | None = None) -> str:
    try:
        result = subprocess.run(argv, cwd=cwd or REPO_ROOT, capture_output=True,
                                text=True, timeout=timeout, check=False, stdin=subprocess.DEVNULL)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise SelfUpdateError(f"操作が実行できないか、タイムアウトしました: {Path(argv[0]).name}") from exc
    if check and result.returncode:
        # Never include stdout/stderr: git error output may contain remote URLs or credentials.
        raise SelfUpdateError(f"操作が失敗しました: {Path(argv[0]).name} (exit {result.returncode})")
    return result.stdout.strip()


def _git(*args: str, timeout: int = 30, check: bool = True) -> str:
    return _cmd(["git", "-C", str(REPO_ROOT), *args], timeout=timeout, check=check)


def _preflight() -> str:
    if os.geteuid() == 0:
        raise SelfUpdateError("rootからControlDeckを更新できません")
    if not (REPO_ROOT / ".git").exists():
        raise SelfUpdateError("Git cloneで導入したControlDeckのみ更新できます")
    if _git("remote", "get-url", "origin") not in _ORIGINS:
        raise SelfUpdateError("originが公式ControlDeckリポジトリではありません")
    if _git("branch", "--show-current") != "main":
        raise SelfUpdateError("mainブランチでのみ更新できます")
    if _git("status", "--porcelain", "--untracked-files=no"):
        raise SelfUpdateError("未コミットの変更があります。保護のため更新を中止します")
    head = _git("rev-parse", "HEAD")
    if not _SHA_RE.fullmatch(head):
        raise SelfUpdateError("現在のGitコミットを確認できません")
    return head


def _remote_release() -> tuple[str, str]:
    request = urllib.request.Request(
        "https://api.github.com/repos/souten-yd/ControlDeck/releases/latest",
        headers={"Accept": "application/vnd.github+json", "User-Agent": "ControlDeck-Updater"},
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            metadata = json.load(response)
        tag = metadata.get("tag_name", "")
        if (metadata.get("draft") or metadata.get("prerelease")
                or not re.fullmatch(r"ControlDeck_v[0-9]+\.[0-9]+(?:\.[0-9]+)?", tag)):
            raise ValueError()
    except Exception as exc:
        raise SelfUpdateError("公式の正式Releaseを確認できません") from exc
    ref = "refs/tags/" + tag
    lines = _git("ls-remote", "origin", ref, ref + "^{}", timeout=30).splitlines()
    refs = dict((parts[1], parts[0]) for line in lines if len(parts := line.split()) == 2)
    sha = refs.get(ref + "^{}", refs.get(ref, ""))
    if not _SHA_RE.fullmatch(sha):
        raise SelfUpdateError("正式Releaseのコミットを確認できません")
    return tag, sha


def _remote_main() -> str:
    # Compatibility for existing callers; the update source is now a release.
    return _remote_release()[1]


def status(*, refresh: bool = True) -> dict:
    state = _saved_state()
    result = {
        "current_sha": "", "remote_sha": "", "update_available": False,
        "can_update": False, "channel": "release", "release_tag": "", "error": "", "job": state,
    }
    try:
        head = _preflight()
        result["current_sha"] = head
        result["can_update"] = True
        if refresh:
            tag, remote = _remote_release()
            result["release_tag"] = tag
            result["remote_sha"] = remote
            result["update_available"] = remote != head
    except SelfUpdateError as exc:
        result["error"] = str(exc)
    return result


def start(expected_sha: str) -> dict:
    """Launch a detached systemd user unit; the server survives until restart."""
    if not _SHA_RE.fullmatch(expected_sha):
        raise SelfUpdateError("更新先コミットが不正です")
    current = status()
    if current["error"] or not current["can_update"]:
        raise SelfUpdateError(current["error"] or "更新できない環境です")
    if current["remote_sha"] != expected_sha or not current["update_available"]:
        raise SelfUpdateError("Releaseが変更されたか、既に最新版です。再確認してください")
    if _cmd(["systemctl", "--user", "is-active", _SERVICE], check=False) != "active":
        raise SelfUpdateError("ControlDeckユーザーサービスが見つかりません")
    _write_state({"phase": "scheduled", "target_sha": expected_sha, "started_at": int(time.time())})
    unit = "control-deck-update-" + expected_sha[:12]
    command = [
        "systemd-run", "--user", "--collect", "--unit", unit,
        "--working-directory", str(REPO_ROOT / "backend"),
        "--description", "Explicit ControlDeck update",
        str(REPO_ROOT / ".venv" / "bin" / "python"), "-m",
        "app.maintenance.self_update", "--apply", expected_sha,
    ]
    try:
        _cmd(command, timeout=15, cwd=REPO_ROOT / "backend")
    except SelfUpdateError:
        _write_state({"phase": "failed", "error": "systemd更新ジョブを開始できません"})
        raise
    return {"scheduled": True, "unit": unit, "target_sha": expected_sha}


def _prepare() -> None:
    # Runs only for the administrator-requested update, never on service startup.
    _cmd(["bash", str(REPO_ROOT / "deck.sh"), "update-prepare"], timeout=1800)


def _health_check() -> None:
    import httpx
    from app.config import get_config
    port = get_config().server.port
    deadline = time.monotonic() + 30
    with httpx.Client(timeout=3, trust_env=False) as client:
        while time.monotonic() < deadline:
            try:
                response = client.get(f"http://127.0.0.1:{port}/api/v1/health")
                if response.status_code == 200 and response.json().get("ok") is True:
                    return
            except (httpx.HTTPError, ValueError, AttributeError):
                pass
            time.sleep(1)
    raise SelfUpdateError("更新後のControlDeckが正常応答しません")


def apply(expected_sha: str) -> None:
    if not _SHA_RE.fullmatch(expected_sha):
        raise SelfUpdateError("更新先コミットが不正です")
    lock_path = _state_path().with_suffix(".lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+") as lock:
        try:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise SelfUpdateError("既に更新中です") from exc
        old = ""
        updated = False
        try:
            old = _preflight()
            _write_state({"phase": "running", "step": "fetch", "old_sha": old,
                          "target_sha": expected_sha, "started_at": int(time.time())})
            # Use the repository's trusted origin and never accept a client-supplied URL/ref.
            tag, release_sha = _remote_release()
            if release_sha != expected_sha:
                raise SelfUpdateError("正式Releaseが変更されました。再確認してください")
            _git("fetch", "--no-tags", "origin", "refs/tags/" + tag, timeout=180)
            fetched = _git("rev-parse", "FETCH_HEAD^{commit}")
            if fetched != expected_sha:
                raise SelfUpdateError("更新中にReleaseのコミットが変わりました。再確認してください")
            _git("merge-base", "--is-ancestor", old, fetched)
            if old == fetched:
                _write_state({"phase": "succeeded", "old_sha": old, "new_sha": old})
                return
            backup = _state_path().parent / ("backup-" + str(int(time.time())))
            backup.mkdir(parents=True, mode=0o700, exist_ok=False)
            _cmd(["bash", str(REPO_ROOT / "deck.sh"), "backup", str(backup)], timeout=300)
            # Do not destroy user data or overwrite local edits.
            _git("merge", "--ff-only", fetched, timeout=60)
            updated = True
            _write_state({"phase": "running", "step": "build", "old_sha": old, "target_sha": fetched})
            _prepare()
            _write_state({"phase": "running", "step": "restart", "old_sha": old, "target_sha": fetched})
            _cmd(["systemctl", "--user", "restart", _SERVICE], timeout=120)
            _cmd(["systemctl", "--user", "is-active", _SERVICE], timeout=15)
            _health_check()
            _write_state({"phase": "succeeded", "old_sha": old, "new_sha": fetched,
                          "finished_at": int(time.time())})
        except Exception as exc:
            error = str(exc) if isinstance(exc, SelfUpdateError) else "更新に失敗しました"
            if updated:
                # Only restore when no other process has modified tracked files.
                try:
                    current = _git("rev-parse", "HEAD")
                    clean = not _git("status", "--porcelain", "--untracked-files=no")
                    if current == expected_sha and clean and old:
                        _git("reset", "--hard", old, timeout=60)
                        _prepare()
                        _cmd(["systemctl", "--user", "restart", _SERVICE], timeout=120)
                        _health_check()
                    else:
                        error += "。作業ツリーが変更されているため手動復旧が必要です"
                except Exception:
                    error += "。自動ロールバックできませんでした"
            _write_state({"phase": "failed", "error": error,
                          "old_sha": old, "target_sha": expected_sha,
                          "finished_at": int(time.time())})
            raise


if __name__ == "__main__":
    if len(sys.argv) != 3 or sys.argv[1] != "--apply":
        raise SystemExit("Usage: python -m app.maintenance.self_update --apply COMMIT_SHA")
    apply(sys.argv[2])
