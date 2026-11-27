"""Nexarch の AI チームの確認のコマンド（T2b・コードが実行して合否を決める。LLM なし）。

報告が取り込まれたとき（自動・「報告を取り込む」・手入力のどれでも）、タスクに checks があれば、
担当のコンソールの作業フォルダで 1 つずつ実行して合否を決める。

- 実行は専用のスレッド（見張りの tick・API の中では待たない）。`shell=True` は使わない
  （shlex.split した引数列。パイプや && は使えない）。環境変数は PATH・HOME・LANG だけ。
- 作業フォルダは Terminal の実体のパス（manager.session_cwd）を commander.workdir.resolve_dir で検証してから使う。
  分からない・検証に通らなければ実行せず `error`（「実行できませんでした」）。
- 結果は task["checks_result"] = {status: running|passed|failed|error, ran_at, results: [{cmd, exit_code, seconds, tail}]}。
  落ちたコマンドで止める（残りは実行しない）。
- 落ちたら（failed）担当へ自動でもう一度送る（リセットしない＝前の文脈を使う）。2 回まで。
  それでも落ちれば status を blocked にして通知（種類 blocked）。T3 で別のモデルへの付け替えを足す前提。
- 監査ログ commander.team.checks にはコマンドの文字列と終了コードだけを入れる（出力は入れない）。
根拠: 利用者の方針（2026-11-27）「勝手に完成している状態を目指して欲しい。ユーザーの負担はない形」と
docs/PLAN-nexarch-team.md（検証できる完了報告・落ちたら自動で直させる）。
"""
from __future__ import annotations

import asyncio
import logging
import os
import shlex
import subprocess
import threading
import time
from pathlib import Path

from app.commander import team_watch, teams, workdir
from app.terminals.manager import manager

logger = logging.getLogger(__name__)

CHECK_TIMEOUT = 600
TAIL_MAX = 2000
_running: set[tuple[str, int]] = set()
_pending_rerun: dict[tuple[str, int], bool] = {}   # ヘッドレスの再挑戦（確認の実行が終わってから始める）
_running_lock = threading.Lock()


def _spawn(func, *args) -> None:
    threading.Thread(target=func, args=args, name="nexarch-checks", daemon=True).start()


def schedule(team_id: str, task_id: int) -> None:
    """確認の実行をスレッドで始める。同じタスクがもう走っていれば何もしない。"""
    key = (team_id, task_id)
    with _running_lock:
        if key in _running:
            return
        _running.add(key)
    _spawn(_run_guarded, team_id, task_id)


def _run_guarded(team_id: str, task_id: int) -> None:
    try:
        run_checks(team_id, task_id)
    except Exception:  # noqa: BLE001 - スレッドの失敗で落とさない。印は error にする
        logger.exception("Nexarch の確認の実行に失敗しました")
        _finish(team_id, task_id, {"status": "error", "ran_at": teams._now(), "results": [],
                                   "message": "実行できませんでした（内部エラー）"})
    finally:
        with _running_lock:
            _running.discard((team_id, task_id))
    if _pending_rerun.pop((team_id, task_id), False):
        from app.commander import team_runner
        try:
            team_runner.start_run(team_id, task_id, "Nexarch", retry=True)
        except teams.TeamError as exc:
            logger.info("ヘッドレスの再挑戦をすぐには始められませんでした（待ちに戻します）: %s", exc)


def _env() -> dict[str, str]:
    env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin")}
    for key in ("HOME", "LANG"):
        if os.environ.get(key):
            env[key] = os.environ[key]
    return env


def _tail(text: str, limit: int = TAIL_MAX) -> str:
    return text[-limit:]


# 出力は一時ファイルへ（メモリに全部を持たない。大量に出すコマンドで backend が膨らまない）。
# ファイルも上限を超えたら切る（ディスクを食い尽くさない）。読むのは最後の TAIL_MAX 字だけ。
OUTPUT_FILE_MAX = 50 * 1024 * 1024


def _read_tail(handle, limit: int = TAIL_MAX) -> str:
    handle.flush()
    size = handle.seek(0, 2)
    handle.seek(max(0, size - limit * 4))
    return handle.read().decode("utf-8", errors="replace")[-limit:]


def _run_one(cmd: str, cwd: Path) -> dict:
    import resource
    import tempfile

    started = time.monotonic()

    def _limit_output() -> None:  # 子のプロセスで、書けるファイルの大きさに上限
        resource.setrlimit(resource.RLIMIT_FSIZE, (OUTPUT_FILE_MAX, OUTPUT_FILE_MAX))

    with tempfile.TemporaryFile() as out_file:
        try:
            argv = shlex.split(cmd)
            if not argv:
                raise ValueError("空のコマンドです")
            done = subprocess.run(  # noqa: S603 - shell=False・引数列・作業フォルダは検証済み
                argv, cwd=str(cwd), timeout=CHECK_TIMEOUT, stdout=out_file, stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL, env=_env(), check=False, preexec_fn=_limit_output,
            )
            code, out = done.returncode, _read_tail(out_file)
        except subprocess.TimeoutExpired:
            code, out = -1, f"（{CHECK_TIMEOUT} 秒でタイムアウト）\n" + _read_tail(out_file)
        except (OSError, ValueError) as exc:
            code, out = 127, f"実行できませんでした: {exc}"
    return {"cmd": cmd, "exit_code": code, "seconds": round(time.monotonic() - started, 2), "tail": _tail(out)}


def _workdir_for(sid: str) -> Path | None:
    try:
        raw = manager.session_cwd(sid)
    except (KeyError, OSError, RuntimeError, ValueError, AttributeError):
        return None
    if not raw:
        return None
    try:
        return workdir.resolve_dir(raw)
    except (OSError, ValueError):  # PermissionError・FileNotFoundError・NotADirectoryError を含む
        return None


def run_checks(team_id: str, task_id: int) -> dict:
    """checks を順に実行して結果を書く（同期。テストは直接呼ぶ）。結果の dict を返す。"""
    with teams._lock:
        task = teams._find_task(teams._find_team(teams._load(), team_id), task_id)
        team = teams._find_team(teams._load(), team_id)
        commands = list(task.get("checks") or [])
        sid = task.get("assignee") or ""
        member = teams.find_member(team, sid) if sid else None
        headless = member is not None and teams.is_headless(member)
        team_dir = str(task.get("cwd") or team.get("workdir") or "")  # worktree の仕事はその worktree で確かめる（T3c）
    if not commands:
        return {}
    if headless:  # ヘッドレスの担当はチームの作業場所で確かめる
        try:
            cwd = workdir.resolve_dir(team_dir) if team_dir else None
        except (OSError, ValueError):
            cwd = None
    else:
        cwd = _workdir_for(sid) if sid else None
    if cwd is None:
        result = {"status": "error", "ran_at": teams._now(), "results": [],
                  "message": "実行できませんでした（作業フォルダが分からない・許可の外）"}
    else:
        rows: list[dict] = []
        for cmd in commands:
            rows.append(_run_one(cmd, cwd))
            if rows[-1]["exit_code"] != 0:
                break  # 落ちたら残りは実行しない（直してからまとめてやり直す）
        ok = len(rows) == len(commands) and all(r["exit_code"] == 0 for r in rows)
        result = {"status": "passed" if ok else "failed", "ran_at": teams._now(), "results": rows}
    _finish(team_id, task_id, result)
    return result


def run_commands(cwd: str, commands: list[str]) -> dict:
    """任意のコマンド列を cwd（検証する）で順に実行して結果を返す（同期。leader の run_checks が使う）。落ちたら残りは実行しない。"""
    try:
        path = workdir.resolve_dir(cwd)
    except (OSError, ValueError):
        return {"status": "error", "ran_at": teams._now(), "results": [], "message": "実行できませんでした（作業フォルダが分からない・許可の外）"}
    rows: list[dict] = []
    for cmd in commands:
        rows.append(_run_one(cmd, path))
        if rows[-1]["exit_code"] != 0:
            break
    ok = len(rows) == len(commands) and all(r["exit_code"] == 0 for r in rows)
    return {"status": "passed" if ok else "failed", "ran_at": teams._now(), "results": rows}


def _audit(team_id: str, task_id: int, result: dict) -> None:
    try:
        from app.audit import service as audit
        from app.database import SessionLocal

        with SessionLocal() as db:
            audit.record(db, "commander.team.checks", username="nexarch", resource_type="commander_team",
                         resource_id=team_id, metadata={
                             "task": task_id, "status": result["status"],
                             "results": [{"cmd": r["cmd"], "exit_code": r["exit_code"]} for r in result.get("results", [])],
                         })
    except Exception:  # noqa: BLE001 - 監査の失敗で確認の結果を失わない
        logger.exception("確認の監査ログを書けませんでした")


def _retry_text(task: dict, attempt: int, result: dict) -> str:
    failed = next((r for r in result["results"] if r["exit_code"] != 0), None)
    detail = f"$ {failed['cmd']}（終了コード {failed['exit_code']}）\n{_tail(failed['tail'], teams.RETRY_TAIL)}" if failed else ""
    return (
        f"【Nexarch タスク #{task['id']} 再挑戦 {attempt}/{teams.MAX_RETRIES}】確認のコマンドが失敗しました。"
        f"直して、同じ 5 行で報告してください。\n{detail}"
    )


def _finish(team_id: str, task_id: int, result: dict) -> None:
    """結果を書く。failed なら自動で再送（2 回まで）、尽きたら blocked＋通知。"""
    blocked_event: dict | None = None
    with teams._lock:
        all_teams = teams._load()
        try:
            team = teams._find_team(all_teams, team_id)
            task = teams._find_task(team, task_id)
        except teams.TeamError:
            return  # 確認の間に消された
        if task.get("status") != "review":
            return  # 差し戻し・合格などで状況が変わった。古い結果は捨てる
        task["checks_result"] = result
        member = teams.find_member(team, str(task.get("assignee") or ""))
        headless = member is not None and teams.is_headless(member)
        rerun: tuple[str] | None = None
        label = {"passed": "合格", "failed": "不合格", "error": "実行できず"}[result["status"]]
        teams._event(task, f"確認: {label}", "Nexarch")
        if result["status"] == "failed":
            attempts = int(task.get("attempts") or 0)
            if attempts < teams.MAX_RETRIES:
                text = _retry_text(task, attempts + 1, result)
                try:
                    if headless:  # 排他の外で実行を始める（下）。新しい会話なので前回の報告を添える
                        previous = (task.get("report") or {}).get("summary") if isinstance(task.get("report"), dict) else ""
                        if previous:
                            text = f"前回の報告: {previous}\n{text}"
                    else:
                        manager.inject_input(task.get("assignee") or "", text, submit=True)
                except (KeyError, OSError, RuntimeError, ValueError) as exc:
                    teams._event(task, f"自動で直させる送信ができませんでした: {exc}", "Nexarch")
                else:
                    task["attempts"] = attempts + 1
                    task["status"] = "todo" if headless else "doing"
                    if headless:
                        task["resume_note"] = text
                        rerun = (text,)
                    for key in ("stalled_at", "limited_at", "closed_noted", "notified"):
                        task.pop(key, None)
                    teams._event(task, f"自動で再挑戦 {attempts + 1}/{teams.MAX_RETRIES}", "Nexarch")
            else:
                task["status"] = "blocked"
                teams._event(task, "状態 review → blocked（自動で直せず）", "Nexarch")
                if team.get("notify", True) is not False:
                    notified = task.setdefault("notified", {})
                    if not notified.get(team_watch.KIND_BLOCKED):
                        notified[team_watch.KIND_BLOCKED] = True
                        blocked_event = {"title": "Nexarch",
                                         "message": team_watch.message_for(task, team_watch.KIND_BLOCKED, "")}
        teams._save(all_teams)
    _audit(team_id, task_id, result)
    if rerun is not None:
        _pending_rerun[(team_id, task_id)] = True
    if blocked_event:
        try:
            asyncio.run(team_watch.send(blocked_event["title"], blocked_event["message"]))
        except Exception:  # noqa: BLE001
            logger.exception("blocked の通知を送れませんでした")
