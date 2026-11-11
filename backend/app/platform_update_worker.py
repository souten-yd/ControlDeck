"""Separate systemd --user updater worker (never imported or executed at startup)."""
from __future__ import annotations

import fcntl
import os
import subprocess
import sys
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from app.config import REPO_ROOT, data_dir, get_config
from app import platform_update as update


class UpdateError(RuntimeError):
    pass


def _step(state: dict, message: str, status: str = "running") -> None:
    update.write_state({
        **state, "status": status, "message": message,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    })


def _run(args: list[str], timeout: int = 900) -> None:
    try:
        cp = subprocess.run(
            args, cwd=REPO_ROOT, env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},
            timeout=timeout, text=True, capture_output=True, check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise UpdateError(f"{Path(args[0]).name} の実行環境または待機時間に問題があります") from exc
    if cp.returncode != 0:
        # Logs may contain local secret path/user information: no raw stdout/stderr.
        raise UpdateError(f"{Path(args[0]).name} が失敗しました（終了コード {cp.returncode}）")


def _health() -> bool:
    try:
        port = int(get_config().server.port)
        req = urllib.request.Request(f"http://127.0.0.1:{port}/api/v1/health")
        with urllib.request.urlopen(req, timeout=2) as response:
            return response.status == 200
    except (OSError, ValueError):
        return False


def _wait_healthy() -> bool:
    for _ in range(45):
        if _health():
            return True
        time.sleep(2)
    return False


def run(tag: str, task_id: str) -> None:
    if not update.TAG_PATTERN.fullmatch(tag):
        raise UpdateError("更新タグの形式が不正です")
    lock = (data_dir() / "maintenance" / "control-deck-update.lock").resolve()
    lock.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.umask(0o077)
    state = update.read_state()
    if state.get("id") != task_id or state.get("target_tag") != tag:
        raise UpdateError("更新ジョブの状態が一致しません")
    with lock.open("a+") as fd:
        try:
            fcntl.flock(fd.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise UpdateError("別の更新処理が実行中です") from exc
        current = update.local_state()
        if not current["ready"] or current["head"] != state["previous_head"]:
            raise UpdateError("開始後に作業ツリーが変更されたため更新を中止しました")
        if update.latest_release()["tag"] != tag:
            raise UpdateError("最新Releaseが変わりました。更新を再確認してください")
        old_head = current["head"]
        changed = False
        try:
            _step(state, "ReleaseのGitタグを検証しています")
            _run(["git", "-C", str(REPO_ROOT), "fetch", "--no-tags", "origin",
                  f"refs/tags/{tag}:refs/tags/{tag}"], timeout=180)
            target_head = update.git("rev-parse", f"refs/tags/{tag}^{{commit}}")
            if target_head == old_head:
                _step(state, "既に最新版です", "succeeded")
                return
            cp = subprocess.run(
                ["git", "-C", str(REPO_ROOT), "merge-base", "--is-ancestor", old_head, target_head],
                capture_output=True, timeout=25, check=False,
            )
            if cp.returncode != 0:
                raise UpdateError("Releaseが現在のmainより新しいfast-forwardコミットではありません")
            _step(state, "設定・DBのバックアップを作成しています")
            backup_dir = (data_dir() / "maintenance" / "preupdate").resolve()
            backup_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
            _run(["bash", str(Path(REPO_ROOT) / "scripts" / "backup.sh"), str(backup_dir)], timeout=900)
            _step(state, "mainを正式リリースへfast-forwardしています")
            _run(["git", "-C", str(REPO_ROOT), "merge", "--ff-only", target_head], timeout=120)
            changed = True
            _step(state, "依存関係とフロントエンドを適用しサービスを再起動しています")
            # npm/pip build is permitted ONLY in this explicitly requested update job.
            # The normal server startup never invokes npm build.
            _run(["bash", str(Path(REPO_ROOT) / "deck.sh"), "service"], timeout=1800)
            _step(state, "更新後のヘルスチェックをしています")
            if not _wait_healthy():
                raise UpdateError("サービスの更新後ヘルスチェックが失敗しました")
            _step({**state, "new_head": target_head}, f"{tag}へ更新しました", "succeeded")
        except Exception as exc:
            problem = str(exc) if isinstance(exc, (RuntimeError, UpdateError)) else "予期しない更新エラー"
            if changed:
                try:
                    _step(state, "更新失敗のため旧版へ復帰しています")
                    # Never destroy concurrent user edits during rollback.
                    if update.git("status", "--porcelain", "--untracked-files=normal"):
                        raise UpdateError("作業ツリーが変更されました。安全のため自動rollbackを停止しました")
                    _run(["git", "-C", str(REPO_ROOT), "reset", "--hard", old_head], timeout=120)
                    _run(["bash", str(Path(REPO_ROOT) / "deck.sh"), "service"], timeout=1800)
                    if not _wait_healthy():
                        raise UpdateError("旧版への復旧後にヘルスチェックが失敗しました")
                    problem += "。前のバージョンに復帰しました"
                except Exception:
                    problem += "。自動復旧できませんでした。バックアップとGit状態を確認してください"
            _step(state, problem, "failed")
            raise


def main() -> None:
    if os.geteuid() == 0:
        raise SystemExit("rootで更新処理を実行しないでください")
    if len(sys.argv) != 3:
        raise SystemExit("usage: python -m app.platform_update_worker <tag> <job_id>")
    run(sys.argv[1], sys.argv[2])


if __name__ == "__main__":
    main()
