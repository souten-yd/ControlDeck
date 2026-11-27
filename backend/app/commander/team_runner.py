"""Nexarch の AI チームのヘッドレスの担い手（T3a・コードが動かす。LLM なし）。

タスクを headless のメンバー（CLI と接続だけ・画面なし・tmux なし）へ割り当てて実行すると、専用スレッドで
CLI を 1 回、新しい会話で走らせ、JSON の行を読んで進み具合・報告・使用量・上限を板へ反映する。

- 実行する CLI（引数は `--help` で確かめた 2026-11-27 の版）:
  - claude: `claude -p <本文> --output-format stream-json --verbose --permission-mode acceptEdits
    --permission-prompts none --allowedTools Bash,Edit,Read,Write,Glob,Grep`（許可に無い道具は聞かずに拒否）。
    アカウントは CLAUDE_CONFIG_DIR（既定のアカウントなら付けない）。
  - codex: `codex exec --json -C <作業フォルダ> -s workspace-write --skip-git-repo-check -o <最後の返事のファイル> <本文>`。
    アカウントは CODEX_HOME。この機械で bwrap（サンドボックス）が使えないとき（Ubuntu の
    kernel.apparmor_restrict_unprivileged_userns=1）は書き込みも shell もすべて失敗するのに「完了」と返すので、
    使えるかを一度だけ調べて覚え（codex_sandbox_ok）、使えなければ `-s danger-full-access`（チームの codex_without_sandbox が
    既定 true のとき。false なら LaunchError）。メンバーの表示と runs に「サンドボックスなし」の印を出す。
  - opencode: ControlDeck の OpenCode（v2）を OpenCode 画面と同じ設定（provider の設定フォルダ・ゲートウェイ・箱）で
    `opencode run <本文> --format json --auto --model controldeck/<モデル> --standalone`。作業フォルダは cwd。
- 作業フォルダはチームの作業場所（teams.ensure_workdir）。commander.workdir.resolve_dir で検証してから使う。
- shell=True は使わない。環境変数は最小限（PATH・HOME・LANG など）＋アカウントのもの。本文・出力はログに出さない。
- 出力は一時ファイル（メモリに全文を持たない・上限 50MB）。読むのは JSON の行ごと。
- 終わったら報告（parse_report。無ければ最後の返事から作る）を板の review へ → T2b の確認のコマンドにつなぐ。
  失敗は自動で再挑戦（2 回まで。前回の報告と失敗の出力を本文に足す）。上限なら team_dispatch が方針（待つ／引き継ぐ）に従う。
- 止める（プロセスのグループごと終わらせる）。ControlDeck を再起動したら走っていた実行は「中断」（recover_interrupted）。
- 監査ログ commander.team.run: 担当・CLI・終了コード・かかった時間・上限か（本文・出力は入れない）。
根拠: 利用者（2026-11-27）「勝手に完成している状態を目指して」「チームがトークンが尽きて止まった場合、作業内容を引き継いで
自動でルーターが別の CLI を開くか、待つかを設定で選択できるように」と docs/PLAN-nexarch-team.md T3。
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import secrets
import shutil
import signal
import subprocess
import tempfile
import threading
import time
from collections import deque
from pathlib import Path

from app.commander import accounts, resume, team_watch, teams, workdir

logger = logging.getLogger(__name__)

RUN_TIMEOUT = 2 * 60 * 60          # 1 回の実行の上限（秒）
POLL_INTERVAL = 0.3
OUTPUT_FILE_MAX = 50 * 1024 * 1024
LINE_MAX = 2 * 1024 * 1024
TAIL_MAX = 2000
CLAUDE_TOOLS = "Bash,Edit,Read,Write,Glob,Grep"
KILL_GRACE = 5.0
WAIT_UNKNOWN = 30 * 60

# 上限・レート制限のエラーの文（失敗したときの JSON のエラー・終了時の文から見分ける）
LIMIT_RE = re.compile(
    r"(usage|rate|5[- ]hour|weekly|session|monthly|daily)[ _-]?limit|limit reached|reached (your|the) .{0,30}limit"
    r"|too many requests|\b429\b|quota|insufficient[_ ]credits?|credit balance|resource[_ ]exhausted", re.I)
_EPOCH_RE = re.compile(r"limit reached\|(\d{10})")

_sandbox_state: dict = {}
_sandbox_lock = threading.Lock()
SANDBOX_PROBE_TIMEOUT = 10
_active: dict[tuple[str, int], dict] = {}
_active_lock = threading.Lock()


class LaunchError(Exception):
    """CLI を起こせない（実行ファイルが無い・アカウントが無い・OpenCode が使えない）。再挑戦しても直らない。"""


def _spawn(func, *args) -> None:
    threading.Thread(target=func, args=args, name="nexarch-run", daemon=True).start()


def codex_sandbox_ok() -> bool:
    """codex のサンドボックス（bwrap）がこの機械で使えるか。一度だけ調べて覚える（shell なし・timeout 付き）。

    bwrap が無ければ調べようがない（codex 自身が持つ）ので使えるものとする。
    """
    with _sandbox_lock:
        if "ok" in _sandbox_state:
            return bool(_sandbox_state["ok"])
        bwrap = shutil.which("bwrap")
        if not bwrap:
            ok = True
        else:
            try:
                done = subprocess.run(  # noqa: S603 - shell=False・引数は固定
                    [bwrap, "--ro-bind", "/", "/", "--dev", "/dev", "true"], capture_output=True,
                    timeout=SANDBOX_PROBE_TIMEOUT, stdin=subprocess.DEVNULL, check=False)
                ok = done.returncode == 0
            except (OSError, subprocess.SubprocessError):
                ok = False
        _sandbox_state["ok"] = ok
        return ok


def member_sandbox_free(member: dict) -> bool:
    """この担い手は、この機械ではサンドボックスなしで動く（codex で bwrap が使えない）。"""
    return member.get("cli") == "codex" and not codex_sandbox_ok()


# ---------------------------------------------------------------- 起動の組み立て

_ENV_KEYS = ("HOME", "LANG", "LC_ALL", "USER", "LOGNAME", "TMPDIR", "XDG_CONFIG_HOME", "XDG_DATA_HOME",
             "XDG_CACHE_HOME", "XDG_STATE_HOME")


def _base_env(exe: str = "") -> dict[str, str]:
    """最小の環境（PATH・HOME・LANG など）。CLI の実行ファイルの場所は PATH の先頭へ足す（node のスクリプトのため）。"""
    path = os.environ.get("PATH", "/usr/bin:/bin")
    if exe and "/" in exe:
        folder = os.path.dirname(exe)
        if folder not in path.split(os.pathsep):
            path = f"{folder}{os.pathsep}{path}"
    env = {"PATH": path[:4096], "NO_COLOR": "1"}
    for key in _ENV_KEYS:
        if os.environ.get(key):
            env[key] = os.environ[key]
    return env


def _opencode_launch(model: str, cwd: Path, text: str):
    """OpenCode（v2）の argv・環境・後片付け。OpenCode 画面と同じ設定（provider）で作る。"""
    from app.integrations.opencode import provider

    try:
        runtime, binary = provider.active_binary()
        settings = provider.get_settings()
        endpoint = str(settings["base_url"]).rstrip("/")
        config = provider._runtime_config(  # noqa: SLF001 - 設定フォルダの作り方を OpenCode 画面と同じにする
            f"team-{secrets.token_hex(4)}", endpoint, model,
            project_id=provider._managed_project_id(cwd), runtime=runtime)  # noqa: SLF001
    except provider.CodeAgentError as exc:
        raise LaunchError(str(exc)) from exc
    try:  # ローカルのモデルは呼ばれたらロードする（run_chat と同じ）
        from app.models_mgmt import local_llm

        asyncio.run(local_llm.ensure_ready_by_base_url(endpoint))
    except Exception:  # noqa: BLE001 - 起動の保証に失敗しても実行は試す
        logger.warning("OpenCode のモデルの起動を保証できませんでした")
    argv = provider._run_argv(runtime, binary, text, model, cwd)  # noqa: SLF001
    argv, env = provider._launch(runtime, argv, cwd, config, interactive=False)  # noqa: SLF001
    return argv, env, lambda: provider._discard_runtime_config(runtime, config)  # noqa: SLF001


ROUTER_TOOLS = "Read,Glob,Grep"


def _model_args(member: dict) -> tuple[str, str]:
    """担い手の model・effort（空なら付けない）。値は画面の入力で検証済みだが、引数に混ぜる前にもう一度確かめる。"""
    from app.commander import models as model_list

    try:
        return model_list.clean_model(member.get("model")), model_list.clean_effort(member.get("effort"))
    except ValueError as exc:
        raise LaunchError(str(exc)) from exc


def build_launch(member: dict, text: str, cwd: Path, last_file: Path, *,
                 allow_no_sandbox: bool = True, schema: dict | None = None) -> tuple[list[str], dict[str, str], object]:
    """(argv, env, cleanup)。CLI ごとの引数の組み立て。実行ファイルは accounts.executable_path。

    model・effort があれば渡す（claude `--model` `--effort`・codex `-m` `-c model_reasoning_effort=`・opencode `--model …#variant`）。
    schema があればルーターの実行（読むだけ・JSON の出力。claude は `--output-format json --json-schema`、codex は `--output-schema`）。
    """
    cli_id = str(member.get("cli") or "")
    model, effort = _model_args(member)
    account = str(member.get("account") or accounts.DEFAULT_ACCOUNT)
    cli = accounts.CLIS.get(cli_id)
    if cli is None or cli_id not in teams.HEADLESS_CLIS:
        raise LaunchError("この CLI はヘッドレスで動かせません")
    if cli_id == "opencode":
        try:
            connection = accounts.opencode_connection(account)
        except FileNotFoundError as exc:
            raise LaunchError(str(exc)) from exc
        argv, extra_env, cleanup = _opencode_launch(model or str(connection["model"]), cwd, text)
        if effort and "--model" in argv:  # variant（モデルが持つときだけ効く）
            at = argv.index("--model") + 1
            argv[at] = f"{argv[at]}#{effort}"
        return argv, {**_base_env(argv[0]), **extra_env}, cleanup
    exe = accounts.executable_path(cli)
    if not exe:
        raise LaunchError(f"{cli.name} が見つかりません")
    try:
        account_env = accounts.env_for(cli, account) or {}
    except (FileNotFoundError, ValueError) as exc:
        raise LaunchError(str(exc)) from exc
    if cli_id == "claude":
        if schema is not None:
            argv = [exe, "-p", text, "--output-format", "json", "--json-schema", json.dumps(schema, ensure_ascii=False),
                    "--permission-mode", "acceptEdits", "--permission-prompts", "none", "--allowedTools", ROUTER_TOOLS]
        else:
            argv = [exe, "-p", text, "--output-format", "stream-json", "--verbose",
                    "--permission-mode", "acceptEdits", "--permission-prompts", "none", "--allowedTools", CLAUDE_TOOLS]
        if model:
            argv += ["--model", model]
        if effort:
            argv += ["--effort", effort]
    else:
        mode = "read-only" if schema is not None else "workspace-write"
        if not codex_sandbox_ok():
            if not allow_no_sandbox:
                raise LaunchError("Codex のサンドボックスがこの機械で使えません")
            mode = "danger-full-access"
        argv = [exe, "exec", "--json", "-C", str(cwd), "-s", mode, "--skip-git-repo-check", "-o", str(last_file)]
        if model:
            argv += ["-m", model]
        if effort:
            argv += ["-c", f"model_reasoning_effort={effort}"]
        if schema is not None:
            schema_file = last_file.with_name("schema.json")
            schema_file.write_text(json.dumps(schema, ensure_ascii=False), encoding="utf-8")
            argv += ["--output-schema", str(schema_file)]
        argv.append(text)
    return argv, {**_base_env(exe), **account_env}, (lambda: None)


# ---------------------------------------------------------------- 出力の JSON の行の読み取り

def _short(value: object, limit: int = 120) -> str:
    return " ".join(str(value).split())[:limit]


class StreamState:
    """CLI の JSON の行を 1 行ずつ読み、進み具合・最後の返事・使用量・エラーを持つ。形が違う行は黙って飛ばす。"""

    def __init__(self, cli: str) -> None:
        self.cli = cli
        self.last_line = ""
        self.tools: deque[str] = deque(maxlen=5)
        self.usage = {"input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0}
        self.final_text = ""
        self.last_text = ""
        self.errors: list[str] = []
        self.is_error = False
        self.limit_hint = False
        self.events = 0
        self.failed_ops = 0          # 失敗した操作（codex の status: failed の item）
        self.writes_ok = 0
        self.writes_failed = 0
        self._seen_items: set[str] = set()

    # -- 共通
    def _text(self, text: str) -> None:
        text = text.strip()
        if not text:
            return
        self.last_text = text
        lines = [ln for ln in text.splitlines() if ln.strip()]
        if lines:
            self.last_line = _short(lines[-1], 200)

    def _tool(self, name: str, detail: object = "") -> None:
        entry = _short(f"{name}: {detail}" if detail else name)
        self.tools.append(entry)
        self.last_line = entry

    def _error(self, value: object) -> None:
        text = _short(value, 500)
        if text and text not in self.errors:
            self.errors.append(text)
        self.is_error = True

    def _add_usage(self, tokens_in: object, tokens_out: object, cost: object = None) -> None:
        for key, value in (("input_tokens", tokens_in), ("output_tokens", tokens_out)):
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                self.usage[key] += int(value)
        if isinstance(cost, (int, float)) and not isinstance(cost, bool):
            self.usage["cost_usd"] = round(self.usage["cost_usd"] + float(cost), 6)

    def feed(self, raw: bytes) -> None:
        try:
            event = json.loads(raw)
        except (ValueError, UnicodeDecodeError):
            return
        if not isinstance(event, dict):
            return
        self.events += 1
        try:
            {"claude": self._claude, "codex": self._codex, "opencode": self._opencode}[self.cli](event)
        except (KeyError, TypeError, ValueError, AttributeError):
            return  # 想定と違う形の行は飛ばす

    # -- claude（stream-json）
    def _claude(self, event: dict) -> None:
        kind = event.get("type")
        if kind == "assistant":
            for block in (event.get("message") or {}).get("content") or []:
                if not isinstance(block, dict):
                    continue
                if block.get("type") == "text":
                    self._text(str(block.get("text") or ""))
                elif block.get("type") == "tool_use":
                    inp = block.get("input") if isinstance(block.get("input"), dict) else {}
                    detail = inp.get("command") or inp.get("file_path") or inp.get("pattern") or ""
                    self._tool(str(block.get("name") or "tool"), detail)
        elif kind == "result":
            self.final_text = str(event.get("result") or "")
            if not self.final_text and event.get("structured_output") is not None:  # --json-schema の結果
                self.final_text = json.dumps(event["structured_output"], ensure_ascii=False)
            if event.get("is_error") or (event.get("subtype") not in (None, "success")):
                self._error(self.final_text or event.get("subtype"))
            usage = event.get("usage") if isinstance(event.get("usage"), dict) else {}
            self._add_usage(
                (usage.get("input_tokens") or 0) + (usage.get("cache_creation_input_tokens") or 0)
                + (usage.get("cache_read_input_tokens") or 0), usage.get("output_tokens"), event.get("total_cost_usd"))
        elif kind == "rate_limit_event":
            info = event.get("rate_limit_info") if isinstance(event.get("rate_limit_info"), dict) else {}
            if str(info.get("status") or "").lower() in ("rejected", "blocked", "exceeded"):
                self.limit_hint = True
        elif kind == "error":
            self._error(event.get("error") or event.get("message"))

    # -- codex（exec --json）
    def _codex(self, event: dict) -> None:
        kind = str(event.get("type") or "")
        item = event.get("item") if isinstance(event.get("item"), dict) else {}
        item_type = item.get("type")
        failed = str(item.get("status") or "") == "failed"
        if kind == "item.completed" and item_type == "agent_message":
            self._text(str(item.get("text") or ""))
            self.final_text = str(item.get("text") or "")
        elif kind in ("item.started", "item.completed") and item_type in ("command_execution", "file_change"):
            if item_type == "command_execution":
                detail = f"shell: {item.get('command')}"
            else:
                changes = [c for c in (item.get("changes") or []) if isinstance(c, dict)]
                detail = "edit: " + ", ".join(f"{c.get('path')} ({c.get('kind')})" for c in changes)
            ident = str(item.get("id"))
            if ident not in self._seen_items:
                self._seen_items.add(ident)
                self._tool(detail + (" 失敗" if failed else ""))
            elif failed:
                self.last_line = _short(detail + " 失敗", 200)
            if kind == "item.completed":
                if failed:
                    self.failed_ops += 1
                if item_type == "file_change":
                    if failed:
                        self.writes_failed += 1
                    elif str(item.get("status") or "completed") == "completed":
                        self.writes_ok += 1
        elif kind == "turn.completed":
            usage = event.get("usage") if isinstance(event.get("usage"), dict) else {}
            self._add_usage(usage.get("input_tokens"), usage.get("output_tokens"))
        elif kind in ("turn.failed", "error"):
            err = event.get("error")
            self._error(err.get("message") if isinstance(err, dict) else (err or event.get("message")))

    # -- opencode（run --format json）
    def _opencode(self, event: dict) -> None:
        kind = str(event.get("type") or "")
        part = event.get("part") if isinstance(event.get("part"), dict) else {}
        if kind == "text":
            self._text(str(part.get("text") or event.get("text") or ""))
            self.final_text = self.last_text
        elif kind == "tool_use":
            state = part.get("state") if isinstance(part.get("state"), dict) else {}
            inp = state.get("input") if isinstance(state.get("input"), dict) else {}
            self._tool(str(part.get("tool") or "tool"), inp.get("command") or inp.get("filePath") or inp.get("pattern") or "")
        elif kind == "step_finish":
            tokens = part.get("tokens") if isinstance(part.get("tokens"), dict) else {}
            self._add_usage(tokens.get("input"), tokens.get("output"), part.get("cost"))
        elif kind == "error":
            err = event.get("error")
            self._error(err.get("message") or err.get("name") or err.get("type") if isinstance(err, dict) else (err or event.get("message")))

    @property
    def writes_all_failed(self) -> bool:
        """書き込み（file_change）を試みたのに 1 つも成功していない。"""
        return self.writes_failed > 0 and self.writes_ok == 0

    def snapshot(self) -> dict:
        return {"last_line": self.last_line, "progress": list(self.tools), "usage": dict(self.usage)}


# ---------------------------------------------------------------- 上限の見分け

def detect_limit(texts: list[str]) -> tuple[bool, float | None]:
    """失敗したときの文から、上限・レート制限か・回復の時刻（epoch 秒。読めなければ None）。"""
    joined = "\n".join(t for t in texts if t)
    if not LIMIT_RE.search(joined):
        return False, None
    epoch = _EPOCH_RE.search(joined)
    if epoch:
        return True, float(epoch.group(1))
    when = resume.parse_reset(joined)
    return True, when.timestamp() if when else None


# ---------------------------------------------------------------- 実行

def _limit_output() -> None:  # 子のプロセスで、書けるファイルの大きさに上限
    import resource

    resource.setrlimit(resource.RLIMIT_FSIZE, (OUTPUT_FILE_MAX, OUTPUT_FILE_MAX))


def _read_lines(path: Path, offset: int, pending: bytes) -> tuple[int, bytes, list[bytes]]:
    """ファイルの offset 以降を読み、完結した行のリストと、まだ途中の端を返す。"""
    try:
        with open(path, "rb") as handle:
            handle.seek(offset)
            chunk = handle.read(4 * 1024 * 1024)
    except OSError:
        return offset, pending, []
    if not chunk:
        return offset, pending, []
    data = pending + chunk
    parts = data.split(b"\n")
    rest = parts.pop()
    if len(rest) > LINE_MAX:
        rest = b""  # 1 行が大きすぎる（読み捨てる）
    return offset + len(chunk), rest, [p for p in parts if p and len(p) <= LINE_MAX]


def _tail_of(path: Path, limit: int = TAIL_MAX) -> str:
    try:
        size = path.stat().st_size
        with open(path, "rb") as handle:
            handle.seek(max(0, size - limit * 4))
            return handle.read().decode("utf-8", errors="replace")[-limit:]
    except OSError:
        return ""


def _kill(proc: subprocess.Popen) -> None:
    try:
        os.killpg(proc.pid, signal.SIGTERM)
    except (ProcessLookupError, PermissionError, OSError):
        pass
    deadline = time.monotonic() + KILL_GRACE
    while proc.poll() is None and time.monotonic() < deadline:
        time.sleep(0.1)
    if proc.poll() is None:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError, OSError):
            pass
        proc.wait()


def _execute(key: tuple[str, int], member: dict, text: str, cwd: Path, allow_no_sandbox: bool = True,
             schema: dict | None = None) -> dict:
    """CLI を 1 回走らせて結果の dict を返す（同期）。本文・出力は返さずログにも出さない。"""
    cli_id = str(member.get("cli") or "")
    tmp = Path(tempfile.mkdtemp(prefix="nexarch-run-"))
    cleanup = None
    try:
        os.chmod(tmp, 0o700)
        out_path, err_path, last_path = tmp / "stdout", tmp / "stderr", tmp / "last"
        argv, env, cleanup = build_launch(member, text, cwd, last_path, allow_no_sandbox=allow_no_sandbox, schema=schema)
        out_fd = os.open(out_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        err_fd = os.open(err_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            proc = subprocess.Popen(  # noqa: S603 - shell=False・引数列・作業フォルダは検証済み
                argv, cwd=str(cwd), env=env, stdin=subprocess.DEVNULL, stdout=out_fd, stderr=err_fd,
                start_new_session=True, preexec_fn=_limit_output, close_fds=True)
        except OSError as exc:
            raise LaunchError(f"起動できませんでした: {exc.strerror or 'OSError'}") from exc
        finally:
            os.close(out_fd)
            os.close(err_fd)
        state = StreamState(cli_id)
        started = time.monotonic()
        with _active_lock:
            entry = _active.get(key)
            if entry is not None:
                entry["proc"] = proc
            stop_early = bool(entry and entry.get("stop"))
        if stop_early:
            _kill(proc)
        offset, pending = 0, b""
        timed_out = False
        while True:
            code = proc.poll()
            offset, pending, lines = _read_lines(out_path, offset, pending)
            for line in lines:
                state.feed(line)
            if lines:
                with _active_lock:
                    if key in _active:
                        _active[key]["live"] = state.snapshot()
            if code is not None:
                offset, pending, lines = _read_lines(out_path, offset, pending)
                for line in lines:
                    state.feed(line)
                if pending.strip():
                    state.feed(pending)
                break
            with _active_lock:
                stopping = bool(_active.get(key, {}).get("stop"))
            if stopping:
                _kill(proc)
            elif time.monotonic() - started > RUN_TIMEOUT:
                timed_out = True
                _kill(proc)
            time.sleep(POLL_INTERVAL)
        code = proc.returncode
        final = state.final_text
        if cli_id == "codex":
            try:
                final = last_path.read_text(encoding="utf-8", errors="replace")[-20000:] or final
            except OSError:
                pass
        stderr_tail = _tail_of(err_path)
        return {"exit": code, "timed_out": timed_out, "state": state, "final": (final or state.last_text)[-20000:],
                "stderr": stderr_tail, "seconds": round(time.monotonic() - started, 1)}
    finally:
        if cleanup is not None:
            try:
                cleanup()
            except Exception:  # noqa: BLE001
                logger.warning("実行の一時の設定を消せませんでした")
        shutil.rmtree(tmp, ignore_errors=True)


def _synth_report(final: str) -> dict:
    """5 行の報告が無かったとき、最後の返事から報告を作る（確認の行は空＝未検証の印が付く）。"""
    lines = [ln.strip() for ln in final.splitlines() if ln.strip()]
    summary = (lines[-1] if lines else "（返事なし）")[:200]
    return {"summary": summary, "changed_files": [], "tests": "", "verify": "", "remaining": "不明（報告の形式なし）"}


def _audit(team_id: str, task_id: int, member: dict, code: int | None, seconds: float, limited: bool, result: str) -> None:
    try:
        from app.audit import service as audit
        from app.database import SessionLocal

        with SessionLocal() as db:
            audit.record(db, "commander.team.run", username="nexarch", resource_type="commander_team",
                         resource_id=team_id, metadata={
                             "task": task_id, "member": teams.member_key(member), "cli": member.get("cli"),
                             "account": member.get("account"), "exit_code": code, "seconds": seconds,
                             "limited": limited, "result": result})
    except Exception:  # noqa: BLE001 - 監査の失敗で実行の結果を失わない
        logger.exception("実行の監査ログを書けませんでした")


def _notify(events: list[dict]) -> None:
    for event in events:
        try:
            asyncio.run(team_watch.send(event["title"], event["message"]))
        except Exception:  # noqa: BLE001
            logger.exception("チームの通知を送れませんでした")


def _worker(team_id: str, task_id: int, run_id: str, member: dict, text: str, cwd: Path,
            allow_no_sandbox: bool = True) -> None:
    key = (team_id, task_id)
    try:
        try:
            result = _execute(key, member, text, cwd, allow_no_sandbox)
        except LaunchError as exc:
            result = {"launch_error": str(exc)[:300]}
        except Exception:  # noqa: BLE001 - スレッドの失敗で落とさない
            logger.exception("Nexarch のヘッドレスの実行に失敗しました")
            result = {"launch_error": "内部エラー"}
        with _active_lock:
            stopped = bool(_active.get(key, {}).get("stop"))
        followup = _complete(team_id, task_id, run_id, member, result, stopped)
    finally:
        with _active_lock:
            _active.pop(key, None)
    if followup:
        followup()
    try:
        from app.commander import team_dispatch
        team_dispatch.kick(team_id)  # 空いた担い手へ待ちのタスクを配る
    except Exception:  # noqa: BLE001
        logger.exception("配り手を動かせませんでした")


def _retry_text(task: dict, attempt: int, why: str, last: str) -> str:
    return (
        f"【Nexarch タスク #{task['id']} 再挑戦 {attempt}/{teams.MAX_RETRIES}】前回の実行が失敗しました（{why}）。"
        f"前回の返事の最後:\n{last[-teams.RETRY_TAIL:]}\n直して、同じ 5 行で報告してください。")


def _complete(team_id: str, task_id: int, run_id: str, member: dict, result: dict, stopped: bool):
    """実行の結果を板へ。後でやること（再挑戦・上限の方針）を関数で返す（排他の外で呼ぶ）。"""
    events: list[dict] = []
    followup = None
    audit_args: tuple | None = None
    with teams._lock:
        all_teams = teams._load()
        try:
            team = teams._find_team(all_teams, team_id)
            task = teams._find_task(team, task_id)
        except teams.TeamError:
            return None  # 実行の間に消された
        run = task.get("run")
        if not isinstance(run, dict) or run.get("run_id") != run_id:
            return None  # 別の実行に置き換わった
        now = teams._now()
        run["ended_at"] = now
        state: StreamState | None = result.get("state")
        usage = dict(state.usage) if state else None
        if state:
            run["last_line"] = state.last_line
            run["progress"] = list(state.tools)
            run["usage"] = usage
            run["failed_ops"] = state.failed_ops
            if state.failed_ops:
                run["failed_note"] = f"失敗した操作 {state.failed_ops} 件"
        code = result.get("exit")
        seconds = float(result.get("seconds") or 0)
        run["exit_code"] = code
        label = f"{member.get('cli')}"

        if "launch_error" in result:
            run["status"] = "failed"
            run["tail"] = result["launch_error"]
            teams.runs_close(task, "failed")
            task["status"] = "blocked"
            teams._event(task, f"実行できませんでした: {result['launch_error']}", "Nexarch")
            audit_args = (code, 0.0, False, "launch_error")
            events += _blocked_event(team, task)
        elif stopped:
            run["status"] = "stopped"
            teams.runs_close(task, "stopped", usage=usage)
            task["status"] = "blocked"
            teams._event(task, "実行を止めた", "Nexarch")
            audit_args = (code, seconds, False, "stopped")
        else:
            failing = code != 0 or bool(state and state.is_error) or result.get("timed_out")
            texts = ((state.errors if state else []) + [result.get("stderr", "")]
                     + ([result.get("final", "")] if failing else []))
            limited, reset_at = detect_limit(texts) if failing else (False, None)
            if failing and not limited and state and state.limit_hint:
                limited, reset_at = True, None
            run["tail"] = (result.get("final") or result.get("stderr") or "")[-TAIL_MAX:]
            if limited:
                run["status"] = "limited"
                run["reset_at"] = reset_at
                task["limited_at"] = now
                teams.runs_close(task, "limited", usage=usage, exit_code=code)
                teams._event(task, f"上限で停止（{label}・ヘッドレス）", "Nexarch")
                audit_args = (code, seconds, True, "limited")
                followup = lambda: _policy_after(team_id, task_id)  # noqa: E731
            elif failing:
                run["status"] = "failed"
                why = "時間切れ" if result.get("timed_out") else f"終了コード {code}"
                teams.runs_close(task, "failed", usage=usage, exit_code=code)
                audit_args = (code, seconds, False, "failed")
                attempts = int(task.get("attempts") or 0)
                if attempts < teams.MAX_RETRIES:
                    task["attempts"] = attempts + 1
                    task["resume_note"] = _retry_text(task, attempts + 1, why, str(result.get("final") or result.get("stderr") or ""))
                    task["status"] = "todo"
                    teams._event(task, f"実行が失敗（{why}）→ 自動で再挑戦 {attempts + 1}/{teams.MAX_RETRIES}", "Nexarch")
                    followup = lambda: _restart(team_id, task_id)  # noqa: E731
                else:
                    task["status"] = "blocked"
                    teams._event(task, f"実行が失敗（{why}）・自動で直せず", "Nexarch")
                    events += _blocked_event(team, task)
            elif state and state.writes_all_failed:
                # 書き込みがすべて失敗したのに「完了」と返した（サンドボックスなど）。報告は未検証・review へ進めない。
                run["status"] = "failed"
                run["reason"] = "write_failed"
                report = teams.parse_report(str(result.get("final") or "")) or _synth_report(str(result.get("final") or ""))
                task["report"] = {**report, "tests": "", "verify": ""}
                teams.runs_close(task, "write_failed", usage=usage, exit_code=code)
                audit_args = (code, seconds, False, "write_failed")
                if int(task.get("write_fail_retries") or 0) < 1:
                    task["write_fail_retries"] = 1
                    task["resume_note"] = (
                        f"【Nexarch タスク #{task['id']} 再挑戦】前回の実行は、ファイルの書き込みがすべて失敗していました"
                        f"（{state.writes_failed} 件）。書き込みが実際にできたかを確かめながら、もう一度作業して同じ 5 行で報告してください。")
                    task["status"] = "todo"
                    teams._event(task, "書き込みがすべて失敗 → 同じ担い手で再挑戦", "Nexarch")
                    followup = lambda: _restart(team_id, task_id)  # noqa: E731
                else:
                    run["status"] = "limited"  # 続けて同じ → 上限と同じく引き継ぎの方針へ（候補が無ければ blocked）
                    task["limited_at"] = now
                    teams._event(task, "書き込みがすべて失敗（再挑戦しても同じ）→ 引き継ぎの方針へ", "Nexarch")
                    followup = lambda: _policy_after(team_id, task_id)  # noqa: E731
            else:
                run["status"] = "finished"
                teams.runs_close(task, "ok", usage=usage, exit_code=code)
                audit_args = (code, seconds, False, "ok")
                report = teams.parse_report(str(result.get("final") or "")) or _synth_report(str(result.get("final") or ""))
                followup = lambda: teams._store_report(team_id, task_id, report, "ヘッドレス", "報告を取り込み（ヘッドレスの実行）")  # noqa: E731
        teams._save(all_teams)
    if audit_args:
        _audit(team_id, task_id, member, *audit_args)
    _notify(events)
    return followup


def _blocked_event(team: dict, task: dict) -> list[dict]:
    if team.get("notify", True) is False:
        return []
    notified = task.setdefault("notified", {})
    if notified.get(team_watch.KIND_BLOCKED):
        return []
    notified[team_watch.KIND_BLOCKED] = True
    return [{"title": "Nexarch", "message": team_watch.message_for(task, team_watch.KIND_BLOCKED, "")}]


def _restart(team_id: str, task_id: int) -> None:
    try:
        start_run(team_id, task_id, "Nexarch", retry=True)
    except teams.TeamError as exc:
        logger.info("再挑戦をすぐには始められませんでした（待ちに戻します）: %s", exc)  # resume_note を持った todo のまま


def _policy_after(team_id: str, task_id: int) -> None:
    from app.commander import team_dispatch

    _notify(team_dispatch.apply_limit_policy(team_id, task_id))


# ---------------------------------------------------------------- 開始・停止・状態

def start_run(team_id: str, task_id: int, by: str, extra: str = "", *, retry: bool = False) -> dict:
    """headless の担当でタスクを実行する（スレッドを始めて、板の表示を返す）。"""
    from app.commander import team_dispatch

    with teams._lock:
        team = teams._find_team(teams._load(), team_id)
        task = teams._find_task(team, task_id)
        if task.get("status") == "done":
            raise teams.TeamError("完了したタスクは送れません", 409)
        member = teams.find_member(team, str(task.get("assignee") or ""))
        if member is None or not teams.is_headless(member):
            raise teams.TeamError("担当がヘッドレスのメンバーではありません")
        with _active_lock:
            if (team_id, task_id) in _active:
                raise teams.TeamError("実行中です", 409)
        reason = team_dispatch.blocker(team, task, member, check_overlap=False, retry=retry)
        if reason:
            raise teams.TeamError(reason, 409)
    sandbox_free = member_sandbox_free(member)  # 初回だけ bwrap を調べる（排他の外）
    allow_no_sandbox = team.get("codex_without_sandbox", True) is not False
    workdir_path = teams.ensure_workdir(team_id)
    try:
        cwd = workdir.resolve_dir(workdir_path)
    except (OSError, ValueError) as exc:  # PermissionError・FileNotFoundError・NotADirectoryError を含む
        raise teams.TeamError(f"チームの作業場所を使えません: {exc}", 409) from exc
    run_id = secrets.token_hex(6)
    key = (team_id, task_id)
    with teams._lock:
        all_teams = teams._load()
        team = teams._find_team(all_teams, team_id)
        task = teams._find_task(team, task_id)
        with _active_lock:
            if key in _active:
                raise teams.TeamError("実行中です", 409)
            _active[key] = {"proc": None, "stop": False, "live": {}, "started": time.monotonic()}
        note = extra or str(task.pop("resume_note", "") or "")
        task.pop("resume_note", None)
        text = teams.dispatch_text(task, str(team.get("brief") or ""))
        if note:
            text += "\n\n" + note
        task["status"] = "doing"
        if not retry:
            task["attempts"] = 0
            task.pop("write_fail_retries", None)
        task.pop("checks_result", None)
        task.pop("wait_until", None)
        task.pop("blocked_reason", None)
        for k in ("stalled_at", "limited_at", "closed_noted", "notified"):
            task.pop(k, None)
        now = teams._now()
        task["run"] = {"run_id": run_id, "status": "running", "member": teams.member_key(member), "cli": member.get("cli"),
                       "started_at": now, "last_line": "", "progress": [], "usage": None, "tail": "", "exit_code": None}
        entry = teams.runs_open(task, member, kind="headless")
        if sandbox_free:
            entry["sandbox"] = "none"
            task["run"]["sandbox"] = "none"
        teams._event(task, ("再挑戦" if retry else "実行を開始") + f"（{member.get('cli')}・ヘッドレス）"
                     + ("・引き継ぎつき" if note and not retry else ""), by)
        teams._save(all_teams)
        view = teams._task_view(team, task)
    _spawn(_worker, team_id, task_id, run_id, dict(member), text, cwd, allow_no_sandbox)
    return view


def stop_run(team_id: str, task_id: int) -> bool:
    """走っている実行を止める。実行中でなければ False。"""
    with _active_lock:
        entry = _active.get((team_id, task_id))
        if entry is None:
            return False
        entry["stop"] = True
        proc = entry.get("proc")
    if proc is not None and proc.poll() is None:
        try:
            os.killpg(proc.pid, signal.SIGTERM)
        except (ProcessLookupError, PermissionError, OSError):
            pass
    return True


def stop_team(team_id: str) -> None:
    with _active_lock:
        keys = [k for k in _active if k[0] == team_id]
    for _team, task_id in keys:
        stop_run(team_id, task_id)


def is_active(team_id: str, task_id: int) -> bool:
    with _active_lock:
        return (team_id, task_id) in _active


def live_overlay(team_id: str, task_id: int, run: dict) -> dict:
    """実行中の表示用。最後の行・道具の呼び出し・使用量（メモリ）と経過時間を重ねる。"""
    with _active_lock:
        entry = _active.get((team_id, task_id))
        live = dict(entry.get("live") or {}) if entry else {}
    out = {**run, **live}
    out["elapsed"] = round(max(0.0, time.time() - float(run.get("started_at") or time.time())), 1)
    return out


def recover_interrupted() -> int:
    """板に実行中と残っているのにプロセスが無い実行（ControlDeck の再起動など）を「中断」にして待ちへ戻す。

    自動で続けはしない（再実行は配り手が方針（auto_run）に従って決める）。
    """
    from app.commander import team_dispatch

    count = 0
    with teams._lock:
        all_teams = teams._load()
        for team in all_teams:
            for task in team.get("tasks", []):
                run = task.get("run")
                if not (isinstance(run, dict) and run.get("status") == "running"):
                    continue
                if is_active(team["id"], task["id"]):
                    continue
                run["status"] = "interrupted"
                run["ended_at"] = teams._now()
                teams.runs_close(task, "interrupted")
                task["status"] = "todo"
                task["resume_note"] = team_dispatch.resume_text(team, task, "ControlDeck の再起動で実行が中断されました")
                teams._event(task, "実行が中断された（ControlDeck の再起動）", "Nexarch")
                count += 1
        if count:
            teams._save(all_teams)
    return count


def git_changes(cwd: Path) -> str:
    """作業フォルダの `git status --short` と `git diff --stat`（shell なし・15 秒）。git が無い・リポジトリでなければ空。"""
    sections: list[str] = []
    env = {k: v for k, v in _base_env().items() if k != "NO_COLOR"}
    env["GIT_TERMINAL_PROMPT"] = "0"
    for args in (("status", "--short"), ("diff", "--stat")):
        try:
            done = subprocess.run(  # noqa: S603 - shell=False・引数は固定・作業フォルダは検証済み
                ["git", *args], cwd=str(cwd), capture_output=True, text=True, timeout=15, env=env,
                check=False, stdin=subprocess.DEVNULL)
        except (OSError, subprocess.SubprocessError):
            return ""
        if done.returncode != 0:
            return ""
        sections.append(f"git {' '.join(args)}:\n{done.stdout.strip() or '（変更なし）'}")
    return "\n\n".join(sections)[:3000]
