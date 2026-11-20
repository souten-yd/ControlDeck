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


HISTORY_LIMIT = 30


def _history_path() -> Path:
    return data_dir().resolve() / "updates" / "history.json"


def history() -> list[dict]:
    """終わった更新の記録（新しい順）。自動か手動か・Release 名・前後のコミット・成否。

    2026-11-20 利用者「自動更新が動いていない気がする」。実際は 11/17 以降で自動 38 回・手動 19 回
    動いていたが、通知先が無く、画面にも前回の更新が出ないので気づけなかった。"""
    try:
        value = json.loads(_history_path().read_text(encoding="utf-8"))
        return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []
    except FileNotFoundError:
        # 履歴を残し始める前の版から上がった直後: 直前の 1 回（状態ファイル）だけは見せる
        state = _saved_state()
        return [_history_entry(state)] if state.get("phase") in ("succeeded", "failed") else []
    except (OSError, ValueError):
        return []


def _history_entry(state: dict[str, Any]) -> dict:
    entry = {key: state.get(key) for key in (
        "phase", "automatic", "release_tag", "old_sha", "new_sha", "target_sha", "error", "started_at", "finished_at")}
    entry["automatic"] = bool(entry["automatic"])
    entry["finished_at"] = entry["finished_at"] or int(time.time())
    return entry


def _record_history(state: dict[str, Any]) -> None:
    """終わった更新（succeeded / failed）を履歴へ足す。履歴を書けなくても更新は止めない。"""
    if state.get("phase") not in ("succeeded", "failed"):
        return
    entry = _history_entry(state)
    try:
        previous = history() if _history_path().exists() else []
        items = [entry, *previous][:HISTORY_LIMIT]
        path = _history_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(items, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, path)
    except OSError:
        pass


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


def _release_names() -> dict[str, str]:
    """コミット → 正式 Release の名前（ControlDeck_vX.Y）。取れなければ空。

    2026-11-20 利用者「更新の画面が数値の羅列で、何から何へ更新するのか分からない。Release の名前で出して」。
    同じコミットに複数の名前があれば、版の大きい方を使う。"""
    try:
        lines = _git("ls-remote", "--tags", "origin", "refs/tags/ControlDeck_v*", timeout=30).splitlines()
    except SelfUpdateError:
        return {}
    names: dict[str, str] = {}
    peeled: dict[str, str] = {}
    for line in lines:
        parts = line.split()
        if len(parts) != 2 or not _SHA_RE.fullmatch(parts[0]):
            continue
        ref = parts[1].removeprefix("refs/tags/")
        tag = ref.removesuffix("^{}")
        if not re.fullmatch(r"ControlDeck_v[0-9]+\.[0-9]+(?:\.[0-9]+)?", tag):
            continue
        (peeled if ref.endswith("^{}") else names)[tag] = parts[0]
    commits = {**names, **peeled}    # 注釈つきタグは ^{} の側がコミット

    def key(tag: str) -> tuple[int, ...]:
        return tuple(int(n) for n in tag.removeprefix("ControlDeck_v").split("."))

    result: dict[str, str] = {}
    for tag in sorted(commits, key=key):
        result[commits[tag]] = tag
    return result


def _remote_main() -> str:
    # Compatibility for existing callers; the update source is now a release.
    return _remote_release()[1]


def status(*, refresh: bool = True) -> dict:
    state = _saved_state()
    result = {
        "current_sha": "", "remote_sha": "", "update_available": False,
        "can_update": False, "channel": "release", "release_tag": "", "current_release_tag": "",
        "error": "", "job": state,
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
            # いまの版の名前（Release のコミットでなければ空。画面はコミットの短い形で出す）
            result["current_release_tag"] = tag if remote == head else _release_names().get(head, "")
    except SelfUpdateError as exc:
        result["error"] = str(exc)
    return result


def start(expected_sha: str, *, automatic: bool = False, release_tag: str = "") -> dict:
    """Launch a detached systemd user unit; the server survives until restart.

    automatic: started by auto_update (L1 policy) rather than by an administrator. The flag
    and the release tag travel through every state write so the restarted server can report
    the outcome once.
    """
    if not _SHA_RE.fullmatch(expected_sha):
        raise SelfUpdateError("更新先コミットが不正です")
    current = status()
    if current["error"] or not current["can_update"]:
        raise SelfUpdateError(current["error"] or "更新できない環境です")
    if current["remote_sha"] != expected_sha or not current["update_available"]:
        raise SelfUpdateError("Releaseが変更されたか、既に最新版です。再確認してください")
    if _cmd(["systemctl", "--user", "is-active", _SERVICE], check=False) != "active":
        raise SelfUpdateError("ControlDeckユーザーサービスが見つかりません")
    _write_state({"phase": "scheduled", "target_sha": expected_sha, "started_at": int(time.time()),
                  "automatic": bool(automatic), "release_tag": release_tag or current.get("release_tag", "")})
    unit = "control-deck-update-" + expected_sha[:12]
    command = [
        "systemd-run", "--user", "--collect", "--unit", unit,
        "--working-directory", str(REPO_ROOT / "backend"),
        "--description", "Automatic ControlDeck update" if automatic else "Explicit ControlDeck update",
        str(REPO_ROOT / ".venv" / "bin" / "python"), "-m",
        "app.maintenance.self_update", "--apply", expected_sha,
    ]
    try:
        _cmd(command, timeout=15, cwd=REPO_ROOT / "backend")
    except SelfUpdateError:
        failed = {"phase": "failed", "error": "systemd更新ジョブを開始できません", "target_sha": expected_sha,
                  "automatic": bool(automatic), "release_tag": release_tag or current.get("release_tag", ""),
                  "finished_at": int(time.time())}
        _write_state(failed)
        _record_history(failed)
        raise
    return {"scheduled": True, "unit": unit, "target_sha": expected_sha}


def _prepare() -> None:
    # Runs only for the administrator-requested update, never on service startup.
    _cmd(["bash", str(REPO_ROOT / "deck.sh"), "update-prepare"], timeout=1800)


def _health_check() -> None:
    """更新後の確認。health だけでは画面やゲートウェイが壊れていても通るので、利用者が
    最初に触る面（画面の配信・meta・ゲートウェイ）まで確かめる。どれかが駄目なら巻き戻す。"""
    import httpx
    from app.config import get_config
    port = get_config().server.port
    base = f"http://127.0.0.1:{port}"
    deadline = time.monotonic() + 60
    with httpx.Client(timeout=5, trust_env=False) as client:
        while time.monotonic() < deadline:
            try:
                response = client.get(f"{base}/api/v1/health")
                if response.status_code == 200 and response.json().get("ok") is True:
                    break
            except (httpx.HTTPError, ValueError, AttributeError):
                pass
            time.sleep(1)
        else:
            raise SelfUpdateError("更新後のControlDeckが正常応答しません")
        try:
            page = client.get(f"{base}/")
            if page.status_code != 200 or 'id="root"' not in page.text:
                raise SelfUpdateError("更新後の画面が配信されません")
            if client.get(f"{base}/api/v1/meta").status_code != 200:
                raise SelfUpdateError("更新後の meta が応答しません")
            from app.models_mgmt import gateway

            key = gateway.get_api_key(create=False)
            if key:
                models = client.get(f"{base}/api/v1/llm/v1/models", headers={"Authorization": f"Bearer {key}"})
                if models.status_code != 200:
                    raise SelfUpdateError("更新後の LLM ゲートウェイが応答しません")
        except httpx.HTTPError as exc:
            raise SelfUpdateError("更新後の確認で接続できません") from exc


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
        # 自動更新の印と Release 名は、途中の状態の書き込みでも保つ（再起動後に結果を一度だけ知らせるため）。
        carried = {key: value for key, value in _saved_state().items() if key in ("automatic", "release_tag")}

        started_at = int(time.time())

        def save(value: dict[str, Any]) -> None:
            state = dict(value, **carried)
            _write_state(state)
            _record_history({"started_at": started_at, **state})

        try:
            old = _preflight()
            save({"phase": "running", "step": "fetch", "old_sha": old,
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
                save({"phase": "succeeded", "old_sha": old, "new_sha": old, "finished_at": int(time.time())})
                return
            backup = _state_path().parent / ("backup-" + str(int(time.time())))
            backup.mkdir(parents=True, mode=0o700, exist_ok=False)
            _cmd(["bash", str(REPO_ROOT / "deck.sh"), "backup", str(backup)], timeout=300)
            # Do not destroy user data or overwrite local edits.
            _git("merge", "--ff-only", fetched, timeout=60)
            updated = True
            save({"phase": "running", "step": "build", "old_sha": old, "target_sha": fetched})
            _prepare()
            save({"phase": "running", "step": "restart", "old_sha": old, "target_sha": fetched})
            _cmd(["systemctl", "--user", "restart", _SERVICE], timeout=120)
            _cmd(["systemctl", "--user", "is-active", _SERVICE], timeout=15)
            _health_check()
            save({"phase": "succeeded", "old_sha": old, "new_sha": fetched,
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
            save({"phase": "failed", "error": error,
                          "old_sha": old, "target_sha": expected_sha,
                          "finished_at": int(time.time())})
            raise


if __name__ == "__main__":
    if len(sys.argv) != 3 or sys.argv[1] != "--apply":
        raise SystemExit("Usage: python -m app.maintenance.self_update --apply COMMIT_SHA")
    apply(sys.argv[2])
