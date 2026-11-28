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
自動でチームリーダーが別の CLI を開くか、待つかを設定で選択できるように」と docs/PLAN-nexarch-team.md T3。
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
HEAD_MAX = 8000   # 最後の返事の先頭（長いレビューの報告の前半。2026-11-28 チームリーダーの要望）
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


def _opencode_launch(model: str, cwd: Path, text: str, *, readonly: bool = False, extra_mcp: dict | None = None,
                     owner_user_id: int = 0, job_id: str = ""):
    """OpenCode（v2）の argv・環境・後片付け。OpenCode 画面と同じ設定（provider）で作る。

    readonly は読み取りの agent（plan）で動かす。extra_mcp は設定の mcp へ足すサーバー（チームリーダーの nexarch_team）。
    owner_user_id があるときは ControlDeck の MCP（作成した利用者の権限・短命 token）を設定へ足す
    （job_id は token の subject に入る識別子。Nexarch の担い手は team-<team>-<task>[-ro]）。"""
    from app.integrations.opencode import model_choice, provider

    # OpenCode（Zen・Go）のモデル（opencode-go/glm-5.3 など）はゲートウェイを通らず、OpenCode が opencode.ai へ
    # 直接つなぐ（鍵は OPENCODE_API_KEY）。設定のモデルはゲートウェイの既定のまま作り、--model だけ Zen を指す。
    # 2026-11-28: 本番の Nexarch で AI チームに GLM を当てようとして、選択肢に出ない・動かせないと分かった。
    zen = model_choice.is_zen(model)
    zen_key = ""
    if zen:
        from app.integrations.opencode import zen as zen_store

        zen_key = zen_store.get()
        if not zen_key:
            raise LaunchError("OpenCode Zen のキーがありません（OpenCode 画面の設定で入れてください）")
    try:
        runtime, binary = provider.active_binary()
        settings = provider.get_settings()
        endpoint = str(settings["base_url"]).rstrip("/")
        config_model = str(settings.get("model") or "auto") if zen else model
        config = provider._runtime_config(  # noqa: SLF001 - 設定フォルダの作り方を OpenCode 画面と同じにする
            job_id or f"team-{secrets.token_hex(4)}", endpoint, config_model,
            owner_user_id=owner_user_id or None, project_id=provider._managed_project_id(cwd), runtime=runtime,
            extra_mcp=extra_mcp)  # noqa: SLF001
    except provider.CodeAgentError as exc:
        raise LaunchError(str(exc)) from exc
    try:  # ローカルのモデルは呼ばれたらロードする（run_chat と同じ）
        from app.models_mgmt import local_llm

        asyncio.run(local_llm.ensure_ready_by_base_url(endpoint))
    except Exception:  # noqa: BLE001 - 起動の保証に失敗しても実行は試す
        logger.warning("OpenCode のモデルの起動を保証できませんでした")
    argv = provider._run_argv(runtime, binary, text, config_model, cwd)  # noqa: SLF001
    if zen:
        argv[argv.index("--model") + 1] = model
    if readonly:
        argv += ["--agent", "plan"]
    env_file: Path | None = None
    extra_env: dict[str, str] = {}
    if zen and provider.isolation_mode() == "box":
        env_file = _secret_env_file({"OPENCODE_API_KEY": zen_key})   # 箱へは --env-file で（argv に値を出さない）
    elif zen:
        extra_env = {"OPENCODE_API_KEY": zen_key}                    # ホストでは子の環境変数だけ（argv に出さない）
    try:
        argv, env = provider._launch(runtime, argv, cwd, config, interactive=False,  # noqa: SLF001
                                     extra_env=extra_env, env_file=env_file)
    except Exception:
        if env_file:
            env_file.unlink(missing_ok=True)
        raise

    def cleanup() -> None:
        if env_file:
            env_file.unlink(missing_ok=True)
        provider._discard_runtime_config(runtime, config)  # noqa: SLF001

    return argv, env, cleanup


def _secret_env_file(values: dict[str, str]) -> Path:
    """秘密の値を渡す --env-file（0600・XDG_RUNTIME_DIR の一時ファイル）。使い終わったら消す。"""
    import tempfile

    directory = os.environ.get("XDG_RUNTIME_DIR") or tempfile.gettempdir()
    fd, name = tempfile.mkstemp(prefix="cd-team-", suffix=".env", dir=directory)   # mkstemp は 0600
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.writelines(f"{key}={value}\n" for key, value in values.items())
    return Path(name)


LEADER_MCP_NAME = "nexarch_team"
LEADER_TIMEOUT = 6 * 60 * 60
# チームリーダーの許す道具: Nexarch の MCP と読むだけ（書かせない・shell なし）
LEADER_TOOLS = f"mcp__{LEADER_MCP_NAME}__*,Read,Glob,Grep"
# 読み取りの担い手（explore・review）の許す道具: 読むのと git の読み取りだけ
READONLY_TOOLS = "Read,Glob,Grep,Bash(git diff:*),Bash(git log:*),Bash(git show:*),Bash(git status:*)"
# 担い手の ControlDeck の MCP（brief C・2026-11-28）: OpenCode 画面の会話と同じ橋渡し（controldeck_addons）。
# 読み取りの担い手の書く道具はサーバー側（agent_mcp）で断る。
WORKER_MCP_NAME = "controldeck_addons"
WORKER_MCP_TOOLS = f"mcp__{WORKER_MCP_NAME}__*"


def _agent_mcp_mounted() -> bool:
    """ControlDeck の MCP の口（/api/v1/addons/agent-mcp）がこの backend にあるか。無ければ橋を渡さない。"""
    try:
        from app.features.registry import opencode_enabled

        return opencode_enabled()
    except Exception:  # noqa: BLE001 - 判定に失敗したときは橋を渡さない
        return False


def tools_mcp_for(team: dict, task: dict) -> dict | None:
    """担い手へ渡す ControlDeck の MCP（brief C・2026-11-28）。

    チームを作った利用者（owner_user_id）の権限で・チームと仕事に結びつく短命 token で渡す。
    作成者の分からないチーム（旧チーム・直作り）と、MCP の口が無いときは付けない。
    OpenCode の担い手は owner_user_id を設定へ渡すだけで（provider が橋と token を作る・OpenCode 画面と同じ）、
    claude・codex は橋渡し（stdio）を argv・設定ファイル・環境変数で渡す。
    """
    owner = int(team.get("owner_user_id") or 0)
    if owner <= 0 or not _agent_mcp_mounted():
        return None
    return {"owner": owner, "team": str(team["id"]), "task": int(task["id"]),
            "readonly": bool(task.get("readonly"))}


def _worker_mcp_spec(tools_mcp: dict) -> dict:
    """担い手の ControlDeck の MCP の橋の指定（token を発行して URL と一緒に返す）。token は argv に出さない。"""
    from app.addons.agent_mcp import agent_mcp_url, issue_worker_token

    return {"url": agent_mcp_url(),
            "token": issue_worker_token(int(tools_mcp["owner"]), str(tools_mcp["team"]), int(tools_mcp["task"]),
                                         readonly=bool(tools_mcp["readonly"]))}


def _model_args(member: dict) -> tuple[str, str]:
    """担い手の model・effort（空なら付けない）。値は画面の入力で検証済みだが、引数に混ぜる前にもう一度確かめる。"""
    from app.commander import models as model_list

    try:
        return model_list.clean_model(member.get("model")), model_list.clean_effort(member.get("effort"))
    except ValueError as exc:
        raise LaunchError(str(exc)) from exc


def leader_mcp_server(mcp: dict, *, claude: bool = False) -> dict:
    """nexarch_team の MCP サーバーの指定（stdio の橋渡し addon_mcp_bridge.py が URL と token で Nexarch の HTTP の口を呼ぶ）。

    claude=True は claude の mcpServers の形（command・args・env）、そうでなければ opencode / codex 共通の形
    （command=[...]・environment）。"""
    import sys

    bridge = str(Path(__file__).resolve().parents[1] / "integrations" / "opencode" / "addon_mcp_bridge.py")
    env = {"CONTROL_DECK_ADDON_MCP_URL": str(mcp["url"]), "CONTROL_DECK_ADDON_MCP_TOKEN": str(mcp["token"])}
    if claude:
        return {"command": sys.executable, "args": [bridge], "env": env}
    return {"type": "local", "command": [sys.executable, bridge], "enabled": True, "timeout": 3_600_000, "environment": env}


def build_launch(member: dict, text: str, cwd: Path, last_file: Path, *,
                 allow_no_sandbox: bool = True, mcp: dict | None = None,
                 readonly: bool = False, tools_mcp: dict | None = None) -> tuple[list[str], dict[str, str], object]:
    """(argv, env, cleanup)。CLI ごとの引数の組み立て。実行ファイルは accounts.executable_path。

    model・effort があれば渡す（claude `--model` `--effort`・codex `-m` `-c model_reasoning_effort=`・opencode `--model …#variant`）。
    mcp があればチームリーダーの実行（`{"url", "token"}`。MCP の橋渡し（stdio）で nexarch_team を渡す。書かせない）。
    tools_mcp があれば担い手の ControlDeck の MCP（`{"owner", "team", "task", "readonly"}`。作成した利用者の権限で
    短命 token を発行。OpenCode は provider の設定へ、claude・codex は橋渡しへ）。
    readonly は explore・review の担い手（書かない: claude は plan＋読む道具だけ・codex は read-only・opencode は plan の agent）。
    token は argv に出さない（claude は 0600 の設定ファイル・codex と opencode は環境変数／0600 の設定）。
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
        extra_mcp = {LEADER_MCP_NAME: leader_mcp_server(mcp)} if mcp else None
        argv, extra_env, cleanup = _opencode_launch(model or str(connection["model"]), cwd, text, readonly=readonly or bool(mcp),
                                                    extra_mcp=extra_mcp,
                                                    owner_user_id=int(tools_mcp["owner"]) if tools_mcp else 0,
                                                    job_id=(f"team-{tools_mcp['team']}-{tools_mcp['task']}"
                                                            + ("-ro" if tools_mcp["readonly"] else "")) if tools_mcp else "")
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
    mcp_env: dict[str, str] = {}
    worker_mcp = _worker_mcp_spec(tools_mcp) if tools_mcp else None
    if cli_id == "claude":
        argv = [exe, "-p", text, "--output-format", "stream-json", "--verbose", "--permission-prompts", "none",
                # 定額のログインだけ（API キーは使わない。accounts.CLAUDE_SUBSCRIPTION_SETTINGS）
                "--settings", json.dumps(accounts.CLAUDE_SUBSCRIPTION_SETTINGS)]
        if mcp:
            config = last_file.with_name("mcp.json")
            fd = os.open(config, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump({"mcpServers": {LEADER_MCP_NAME: {"type": "stdio", **leader_mcp_server(mcp, claude=True)}}}, handle)
            argv += ["--permission-mode", "dontAsk", "--allowedTools", LEADER_TOOLS, "--mcp-config", str(config), "--strict-mcp-config"]
        else:
            if worker_mcp:  # 担い手の ControlDeck の MCP: 0600 の設定ファイル（token は中だけ）
                config = last_file.with_name("mcp.json")
                fd = os.open(config, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
                with os.fdopen(fd, "w", encoding="utf-8") as handle:
                    json.dump({"mcpServers": {WORKER_MCP_NAME: {"type": "stdio", **leader_mcp_server(worker_mcp, claude=True)}}}, handle)
                argv += ["--mcp-config", str(config), "--strict-mcp-config"]
            if readonly:
                argv += ["--permission-mode", "plan",
                         "--allowedTools", READONLY_TOOLS + (f",{WORKER_MCP_TOOLS}" if worker_mcp else "")]
            else:
                argv += ["--permission-mode", "acceptEdits",
                         "--allowedTools", CLAUDE_TOOLS + (f",{WORKER_MCP_TOOLS}" if worker_mcp else "")]
        if model:
            argv += ["--model", model]
        if effort:
            argv += ["--effort", effort]
    else:
        mode = "read-only" if (readonly or mcp) else "workspace-write"
        if not codex_sandbox_ok():
            if not allow_no_sandbox:
                raise LaunchError("Codex のサンドボックスがこの機械で使えません")
            mode = "danger-full-access"
        argv = [exe, "exec", "--json", "-C", str(cwd), "-s", mode, "--skip-git-repo-check", "-o", str(last_file)]
        if model:
            argv += ["-m", model]
        if effort:
            argv += ["-c", f"model_reasoning_effort={effort}"]
        if mcp:
            spec = leader_mcp_server(mcp)
            name = LEADER_MCP_NAME
            argv += ["-c", f"mcp_servers.{name}.command={json.dumps(spec['command'][0])}",
                     "-c", f"mcp_servers.{name}.args={json.dumps(spec['command'][1:])}",
                     "-c", f"mcp_servers.{name}.env_vars={json.dumps(sorted(spec['environment']))}",
                     "-c", f"mcp_servers.{name}.tool_timeout_sec=3600",
                     # codex exec の承認は never なので、MCP の道具は既定で「承認が要る」として失敗する（2026-11-27 実機:
                     # "MCP tool call requires approval, but approval policy is never"）。nexarch_team だけを承認済みにする
                     "-c", f'mcp_servers.{name}.default_tools_approval_mode="approve"']
            mcp_env = dict(spec["environment"])  # token は argv ではなく codex の環境変数で渡し、env_vars で橋渡しへ転送する
        elif worker_mcp:
            spec = leader_mcp_server(worker_mcp)
            name = WORKER_MCP_NAME
            argv += ["-c", f"mcp_servers.{name}.command={json.dumps(spec['command'][0])}",
                     "-c", f"mcp_servers.{name}.args={json.dumps(spec['command'][1:])}",
                     "-c", f"mcp_servers.{name}.env_vars={json.dumps(sorted(spec['environment']))}",
                     "-c", f"mcp_servers.{name}.tool_timeout_sec=3600",
                     "-c", f'mcp_servers.{name}.default_tools_approval_mode="approve"']
            mcp_env = dict(spec["environment"])
        argv += [*accounts.CODEX_SUBSCRIPTION_ARGS, text]   # ChatGPT のログインだけ（API キーは使わない）
    return argv, accounts.without_api_keys({**_base_env(exe), **account_env, **mcp_env}), (lambda: None)


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

def failure_reason(result: dict) -> str:
    """失敗の理由（短い言葉）。シグナルで落ちたときは名前を添える（-25 だけでは分からない）。"""
    if result.get("too_large"):
        return f"出力が {OUTPUT_FILE_MAX // (1024 * 1024)}MB を超えたので止めました"
    code = result.get("exit")
    if isinstance(code, int) and code < 0:
        import signal

        try:
            return f"終了コード {code}（シグナル {signal.Signals(-code).name}）"
        except ValueError:
            pass
    return f"終了コード {code}"


def output_too_large(*paths: Path, limit: int = OUTPUT_FILE_MAX) -> bool:
    """出力のファイルのどれかが上限を超えたか。

    以前は子のプロセスに RLIMIT_FSIZE を掛けていたが、それは子が書く「すべての」ファイルに効く。
    2026-11-28 本番で、Codex が自分の記録（~/.codex/logs_2.sqlite 66MB・thread_history 1GB）を書いた瞬間に
    SIGXFSZ（終了コード -25）で落ち、チームリーダーが 1 秒で止まった。出力のファイルだけを見張って止める。"""
    for path in paths:
        try:
            if path.stat().st_size > limit:
                return True
        except OSError:
            continue
    return False


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
             *, mcp: dict | None = None, readonly: bool = False, timeout: float = RUN_TIMEOUT,
             tools_mcp: dict | None = None) -> dict:
    """CLI を 1 回走らせて結果の dict を返す（同期）。本文・出力は返さずログにも出さない。"""
    cli_id = str(member.get("cli") or "")
    tmp = Path(tempfile.mkdtemp(prefix="nexarch-run-"))
    cleanup = None
    try:
        os.chmod(tmp, 0o700)
        out_path, err_path, last_path = tmp / "stdout", tmp / "stderr", tmp / "last"
        argv, env, cleanup = build_launch(member, text, cwd, last_path, allow_no_sandbox=allow_no_sandbox, mcp=mcp,
                                         readonly=readonly, tools_mcp=tools_mcp)
        out_fd = os.open(out_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        err_fd = os.open(err_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            proc = subprocess.Popen(  # noqa: S603 - shell=False・引数列・作業フォルダは検証済み
                argv, cwd=str(cwd), env=env, stdin=subprocess.DEVNULL, stdout=out_fd, stderr=err_fd,
                start_new_session=True, close_fds=True)
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
        too_large = False
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
            elif time.monotonic() - started > timeout:
                timed_out = True
                _kill(proc)
            elif not too_large and output_too_large(out_path, err_path):
                too_large = True   # 出力が上限を超えた（ディスクを食い尽くさない）。止めて失敗にする
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
        return {"exit": code, "timed_out": timed_out, "too_large": too_large, "state": state, "final": (final or state.last_text)[-20000:],
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
            asyncio.run(team_watch.send(event["title"], event["message"], team_watch.notify_kind(event)))
        except Exception:  # noqa: BLE001
            logger.exception("チームの通知を送れませんでした")


def _worker(team_id: str, task_id: int, run_id: str, member: dict, text: str, cwd: Path,
            allow_no_sandbox: bool = True, readonly: bool = False, tools_mcp: dict | None = None) -> None:
    key = (team_id, task_id)
    try:
        try:
            result = _execute(key, member, text, cwd, allow_no_sandbox, readonly=readonly, tools_mcp=tools_mcp)
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
            failing = code != 0 or bool(state and state.is_error) or result.get("timed_out") or result.get("too_large")
            texts = ((state.errors if state else []) + [result.get("stderr", "")]
                     + ([result.get("final", "")] if failing else []))
            limited, reset_at = detect_limit(texts) if failing else (False, None)
            if failing and not limited and state and state.limit_hint:
                limited, reset_at = True, None
            final_text = str(result.get("final") or result.get("stderr") or "")
            run["tail"] = final_text[-TAIL_MAX:]
            run["head"] = final_text[:HEAD_MAX] if len(final_text) > TAIL_MAX else ""
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
                why = "時間切れ" if result.get("timed_out") else failure_reason(result)
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
                queued = str(task.pop("queued_note", "") or "")
                if queued:
                    # 走っている間にチームリーダーが足した指示（delegate の note）。報告は残し、その指示でもう一度走らせる
                    task["report"] = report
                    task["status"] = "todo"
                    task["resume_note"] = queued[:teams.TEXT_MAX]
                    teams._event(task, "走っている間に足された指示で、もう一度", "Nexarch")  # noqa: SLF001
                    followup = lambda: _restart(team_id, task_id)  # noqa: E731
                else:
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

# 守りの点検表（2026-11-28 品質の評価 A1: AI チームのコードで、パス・リンクの守りの見落としが 2 回あり、
# チームの中のレビューは見つけなかった）。review の担い手の本文に必ず付ける。
SECURITY_CHECKLIST = (
    "【守りの点検表】差分を次の観点で必ず確かめ、見つけたものは報告の「残り」に書くこと:\n"
    "- パス: 利用者や AI から来るパスを realpath で正規化し、許可の範囲の中か確かめているか（.. ・絶対パス・空）。\n"
    "- シンボリックリンク: リンクをたどって許可の外（~/.ssh・data_dir・秘密の置き場）を読み書き・コピーしないか。\n"
    "- 秘密: 鍵・トークン・パスワードをログ・argv・画面・応答・ファイルの写しに出していないか。\n"
    "- 権限: API・MCP の道具ごとに認証と権限を確かめているか。読み取りの役割に書く道具を渡していないか。\n"
    "- 外部: 外への通信・取得（npm・pip・モデル・ブラウザ）を勝手にしていないか。\n"
    "- 実行: shell=True・os.system・文字列の連結でコマンドを作っていないか。root で動かしていないか。"
)


def task_notes(readonly: bool, tier: str) -> str:
    """担い手の本文の後ろに付ける決まり（読み取り・調べ方・点検表・コミット）。"""
    notes = []
    if readonly:
        notes.append("【読み取りだけ】ファイルを作らない・変えない・消さない。調べた結果と評価を報告に書くこと。")
        # 2026-11-28 品質の評価 S2: 調べもの 1 本が入力 8〜12 万トークン・17〜33 分かかっていた
        notes.append("【調べ方】ファイル全体を何本も読み込まない。grep・ファイルの一覧・行の範囲で、必要な所だけ読むこと。")
    if tier == "review":
        notes.append(SECURITY_CHECKLIST)
    if not readonly:
        notes.append("【コミット】コミットはしなくてよい（箱の中では本体の .git は読み取り専用）。変更をファイルに残せば、"
                     "結果を集めるときに Nexarch がコミットする。")
    return "".join("\n\n" + n for n in notes)


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
        if team.get("local_only"):  # 閉じたチーム: ローカル LLM の担い手だけ（brief C・2026-11-28）
            problem = teams.member_local_problem(member)
            if problem:
                raise teams.TeamError(problem)
        with _active_lock:
            if (team_id, task_id) in _active:
                raise teams.TeamError("実行中です", 409)
        reason = team_dispatch.blocker(team, task, member, check_overlap=False, retry=retry)
        if reason:
            raise teams.TeamError(reason, 409)
    sandbox_free = member_sandbox_free(member)  # 初回だけ bwrap を調べる（排他の外）
    allow_no_sandbox = team.get("codex_without_sandbox", True) is not False
    workdir_path = teams.ensure_workdir(team_id)
    with teams._lock:
        own = str(teams._find_task(teams._find_team(teams._load(), team_id), task_id).get("cwd") or "")
    try:  # worktree の仕事はその worktree（T3c）
        cwd = workdir.resolve_dir(own or workdir_path)
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
        readonly = bool(task.get("readonly"))
        from app.commander import team_roles

        role = team_roles.find_role(team, task.get("role")) if task.get("role") else None
        text += task_notes(readonly, str((role or {}).get("name") or ""))
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
        worker_tools = tools_mcp_for(team, task)
        teams._event(task, ("再挑戦" if retry else "実行を開始") + f"（{member.get('cli')}・ヘッドレス）"
                     + ("・引き継ぎつき" if note and not retry else ""), by)
        teams._save(all_teams)
        view = teams._task_view(team, task)
    _spawn(_worker, team_id, task_id, run_id, dict(member), text, cwd, allow_no_sandbox, readonly, worker_tools)
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
