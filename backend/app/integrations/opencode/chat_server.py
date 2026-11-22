"""AI アシスタントから使う OpenCode v2 のサーバー（`opencode serve`）。

これまでチャットの OpenCode は発言ごとに `opencode run --auto --standalone` を起こし、本文の文字だけを
受け取っていた。ここでは OpenCode 2.0 の全クライアント（TUI・デスクトップ・Web）が使う API へ直接つなぎ、
本文・思考・道具の実行（入力・出力）・許可の問い合わせ・中断をそのままチャットへ流す。

- サーバーは **プロジェクト × 利用者ごとに 1 つ**。今の隔離（プロジェクトの箱では書けるのはその
  プロジェクトだけ）を保つため。systemd のユーザーユニットで起こし、Web プロセスの子にはしない。
  ControlDeck の再起動・更新の間も OpenCode の作業は続く。30 分使われなければ止める。
- パスワードは環境変数ファイル（0600）で渡し、argv に載せない。状態ファイル（0600）にも置くが、
  ログ・画面・API の応答には出さない。
- 権限は `plugins/controldeck-permission` が決める（モードは設定ディレクトリの JSON、画面から切替）。
  判断させる操作を ask にするのは provider._apply_chat_server。
- 実測: docs と memory の「opencode-v2-service-api-verified」（v2.3.0、ネットワークを切った箱で確認）。
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import threading
import logging
import os
import re
import secrets
import shutil
import socket
from urllib.parse import quote
import subprocess
import time
from collections import deque
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

logger = logging.getLogger("control_deck.opencode.chat_server")

PORT_BASE = 18300
PORT_SPAN = 100
IDLE_SECONDS = 30 * 60
START_TIMEOUT = 60.0
TOOL_OUTPUT_LIMIT = 4000
_locks: dict[str, asyncio.Lock] = {}
# いま発言を処理中のサーバー（このプロセス内）。掃除の対象から外す。
_active: dict[str, int] = {}
TOUCH_SECONDS = 60


class ChatServerError(RuntimeError):
    pass


@dataclass
class Server:
    key: str
    port: int
    password: str
    unit: str
    config: str
    project: str
    permission_file: str
    started_at: float
    last_used: float
    owner: int | None = None
    # 作業場（開発用の箱）の中で動くサーバーなら、その箱の名前。空は今までのプロジェクトの箱
    workspace: str = ""

    @property
    def base(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def headers(self) -> dict[str, str]:
        token = base64.b64encode(f"opencode:{self.password}".encode()).decode()
        # HTTP のヘッダーは ASCII だけ。日本語のプロジェクト名（例: コンバータシム）でそのまま送ると、送る前に
        # UnicodeEncodeError で落ちる（2026-11-17）。OpenCode 自身も encodeURIComponent で送り、受け側で戻している
        return {"Authorization": f"Basic {token}",
                "x-opencode-directory": quote(self.project, safe="/-_.~")}


# ---- 状態 ----------------------------------------------------------------------------------

def _state_dir() -> Path:
    from app.integrations.opencode.provider import _integration_dir

    path = _integration_dir() / "chat-servers"
    path.mkdir(parents=True, exist_ok=True)
    os.chmod(path, 0o700)
    return path


def server_key(project: Path, owner_user_id: int | None) -> str:
    return hashlib.sha256(f"{owner_user_id}:{project.resolve()}".encode()).hexdigest()[:12]


def _state_path(key: str) -> Path:
    if not key.isalnum():
        raise ChatServerError("不正なサーバーキーです")
    return _state_dir() / f"{key}.json"


def load(key: str) -> Server | None:
    try:
        data = json.loads(_state_path(key).read_text(encoding="utf-8"))
        return Server(**data)
    except (OSError, ValueError, TypeError):
        return None


def _save(server: Server) -> None:
    path = _state_path(server.key)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.{secrets.token_hex(4)}.tmp")
    tmp.write_text(json.dumps(server.__dict__), encoding="utf-8")
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)


def _all() -> list[Server]:
    servers = []
    for path in _state_dir().glob("*.json"):
        server = load(path.stem)
        if server is not None:
            servers.append(server)
    return servers


def _free_port() -> int:
    used = {s.port for s in _all()}
    for port in range(PORT_BASE, PORT_BASE + PORT_SPAN):
        if port in used:
            continue
        with socket.socket() as sock:
            if sock.connect_ex(("127.0.0.1", port)) != 0:
                return port
    raise ChatServerError("OpenCode のサーバーに使えるポートがありません")


# ---- 権限モード --------------------------------------------------------------------------------

def set_mode(server: Server, mode: str) -> None:
    from app.integrations.opencode.provider import CHAT_PERMISSION_MODES

    if mode not in CHAT_PERMISSION_MODES:
        raise ChatServerError("権限モードは auto / ask のどちらかです")
    _update_permission_file(server, lambda data: {**data, "mode": mode})


_permission_lock = threading.Lock()
PLAN_SESSIONS_KEPT = 256
_session_agents: dict[tuple[str, str], str] = {}   # 直近に切り替えたエージェント（同じなら切り替えない）


def _update_permission_file(server: Server, change: Callable[[dict], dict]) -> None:
    """権限プラグインが読むファイル（{"mode", "plan_sessions"}）を書き換える。プラグインは呼ばれるたびに読む。"""
    path = Path(server.permission_file)
    with _permission_lock:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                data = {}
        except (OSError, ValueError):
            data = {}
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(change(data)), encoding="utf-8")
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)


def mark_plan_session(server: Server, session_id: str, plan: bool) -> None:
    """プランで動くセッションを権限プラグインへ知らせる。プラグインはエージェント名に加えてこれでも判断する
    （エージェント名は省略されうる。レビュー指摘 2026-11-18）。"""
    def change(data: dict) -> dict:
        sessions = [str(s) for s in data.get("plan_sessions") or [] if str(s) != session_id]
        if plan:
            sessions.append(session_id)
        return {**data, "plan_sessions": sessions[-PLAN_SESSIONS_KEPT:]}

    _update_permission_file(server, change)


def apply_mode_to_running(mode: str) -> int:
    """動いている全サーバーへ権限モードをすぐ効かせる（設定は全体で 1 つ。起こし直さない）。"""
    count = 0
    for server in _all():
        set_mode(server, mode)
        count += 1
    return count


def get_mode(server: Server) -> str:
    try:
        mode = json.loads(Path(server.permission_file).read_text(encoding="utf-8")).get("mode")
    except (OSError, ValueError):
        return "ask"
    return mode if mode in ("auto", "ask") else "ask"


# ---- 起動・停止 --------------------------------------------------------------------------------

async def _healthy(server: Server) -> bool:
    try:
        async with httpx.AsyncClient(timeout=3, trust_env=False) as client:
            response = await client.get(f"{server.base}/api/info", headers=server.headers())
        return response.status_code == 200
    except httpx.HTTPError:
        return False


def _unit_active(unit: str) -> bool:
    systemctl = shutil.which("systemctl")
    if systemctl is None:
        return False
    result = subprocess.run([systemctl, "--user", "is-active", f"{unit}.service"],
                            capture_output=True, text=True, timeout=10, check=False)
    return result.stdout.strip() in ("active", "activating")


def _stop_unit(unit: str) -> None:
    systemctl = shutil.which("systemctl")
    if systemctl is not None:
        subprocess.run([systemctl, "--user", "stop", f"{unit}.service"],
                       capture_output=True, timeout=30, check=False)


def stop(key: str) -> None:
    server = load(key)
    if server is None:
        return
    _stop_unit(server.unit)
    _state_path(key).unlink(missing_ok=True)
    if server.workspace:
        # 設定は箱のホームの中（箱ごと残る）。消すのはパスワードの env-file と権限のファイルだけ
        _workspace_env_file(key).unlink(missing_ok=True)
        Path(server.permission_file).unlink(missing_ok=True)
        return
    shutil.rmtree(server.config, ignore_errors=True)


def reap_idle(now: float | None = None, *, keep: str = "") -> list[str]:
    """しばらく使われていないサーバーを止める（ensure のたびに呼ぶ）。"""
    now = now or time.time()
    stopped = []
    for server in _all():
        if server.key != keep and server.key not in _active and now - server.last_used > IDLE_SECONDS:
            stop(server.key)
            stopped.append(server.key)
    return stopped


REAP_INTERVAL_SECONDS = 5 * 60


async def idle_reap_loop() -> None:
    """使われていないサーバーを数分おきに止める。

    前は次の会話を始めたとき（ensure）にだけ reap_idle を呼んでいたので、最後に使ったサーバーは
    止まらずに残った（2026-11-21 実機: 17.5 時間使われていない箱が host network のまま 21 時間動いていた）。
    """
    import asyncio

    while True:
        await asyncio.sleep(REAP_INTERVAL_SECONDS)
        try:
            stopped = await asyncio.to_thread(reap_idle)
            if stopped:
                logger.info("使われていない OpenCode の会話サーバーを止めました: %s", ", ".join(stopped))
        except Exception:  # noqa: BLE001 - 1 回の失敗で見張りを止めない
            logger.exception("OpenCode の会話サーバーの片付けに失敗しました")


def touch(server: Server) -> None:
    server.last_used = time.time()
    _save(server)


def _start(project: Path, owner_user_id: int | None, key: str) -> Server:
    """設定を書き、systemd のユーザーユニットで `opencode serve` を起こす（同期。to_thread で呼ぶ）。"""
    from app.integrations.opencode import provider

    settings = provider.get_settings()
    runtime, binary = provider.active_binary()
    systemd_run = shutil.which("systemd-run")
    if systemd_run is None:
        raise ChatServerError("systemd user manager を利用できません")
    endpoint = str(settings["base_url"]).rstrip("/")
    model = str(settings["model"]).strip()
    config = provider._runtime_config(
        f"chat-{key}", endpoint, model, owner_user_id=owner_user_id,
        project_id=provider._managed_project_id(project), runtime=runtime, chat_server=True,
    )
    permission_file = config / "opencode" / "controldeck-permission.json"
    port = _free_port()
    password = secrets.token_urlsafe(24)
    env_file = config / "serve.env"
    env_file.write_text(f"OPENCODE_PASSWORD={password}\n", encoding="utf-8")
    os.chmod(env_file, 0o600)
    server = Server(key=key, port=port, password=password, unit=f"cd-opencode-chat-{key}",
                    config=str(config), project=str(project), permission_file=str(permission_file),
                    started_at=time.time(), last_used=time.time(), owner=owner_user_id)
    set_mode(server, str(settings.get("chat_permission_mode") or "auto"))
    command = [str(binary), "serve", "--hostname", "127.0.0.1", "--port", str(port)]
    command, launch_env = provider._launch(
        runtime, command, project, config, interactive=False,
        extra_env={"CONTROLDECK_PERMISSION_FILE": str(permission_file),
                   "CONTROLDECK_ISOLATION": provider.isolation_mode()}, env_file=env_file,
    )
    _stop_unit(server.unit)  # 前回の残り（状態ファイルを失ったもの）
    argv = [systemd_run, "--user", "--quiet", "--collect", f"--unit={server.unit}",
            f"--working-directory={project}",
            *(["--property", f"EnvironmentFile={env_file}"] if launch_env else []),
            *(f"--setenv={k}={v}" for k, v in launch_env.items()), *command]
    result = subprocess.run(argv, capture_output=True, text=True, timeout=30, check=False)
    if result.returncode != 0:
        shutil.rmtree(config, ignore_errors=True)
        raise ChatServerError(f"OpenCode のサーバーを起動できませんでした（{result.stderr.strip()[:200]}）")
    _save(server)
    return server


def _workspace_env_file(key: str) -> Path:
    return _state_dir() / f"{key}.env"


def workspace_box(project: Path, owner_user_id: int | None):
    """プロジェクトが作業場ならその箱（持ち主の会話だけ）。作業場でなければ None。

    持ち主（meta の owner）と会話の利用者が一致しなければ断る（他人の作業場へ入れない）。
    """
    from app.maintenance import sandbox, workspaces

    box = workspaces.box_for_project(project)
    if box is None:
        return None
    try:
        expected = sandbox._owner_user_id(box.owner) if box.owner else None
    except sandbox.SandboxError:
        expected = None
    if owner_user_id is None or expected is None or expected != owner_user_id:
        raise ChatServerError("この作業場は、作った人だけが使えます")
    return box


def _start_workspace(box, owner_user_id: int | None, key: str, *, network: str = "gateway") -> Server:
    """作業場（開発用の箱）の pod の中で `opencode serve` を起こす（同期。to_thread で呼ぶ）。

    設定は sandbox.opencode_config（中継を向く・鍵が無い・開発のスキル・レビュー役・モデルの順番の最初）に、
    会話サーバー用の部分（権限プラグイン・ask・question の deny・todo）を足して箱のホームへ書く。
    ControlDeck からは pod が公開する 127.0.0.1:<serve_port> で話す。パスワードは env-file（argv に出さない）。

    network: 今は "gateway"（中継だけに届く pod）のみ。秘密の会話用に "none"（network を持たない箱）を
    段 2 で足すための引数。未対応の値は断る。
    """
    from app.integrations.opencode import provider
    from app.maintenance import sandbox, workspaces

    if network != "gateway":
        raise ChatServerError(f"作業場の network は {network} にまだ対応していません")
    systemd_run = shutil.which("systemd-run")
    if systemd_run is None:
        raise ChatServerError("systemd user manager を利用できません")
    _, binary = provider.active_binary()
    try:
        chain = sandbox.model_chain(box)
        if not chain:
            raise ChatServerError(sandbox.NO_MODEL_MESSAGE)
        chosen = chain[0]
        sandbox.opencode_config(box, model="auto" if "/" in chosen else chosen, work_model=chosen,
                                chat_server=True, default_model=chosen)
    except sandbox.SandboxError as exc:
        raise ChatServerError(str(exc)) from exc
    config_dir = box.home / ".config" / "opencode"
    permission_file = config_dir / "controldeck-permission.json"
    port = box.serve_port
    password = secrets.token_urlsafe(24)
    # パスワードと（使えるなら）Zen のキーを 1 つの env-file で渡す。箱のフォルダの外・0600・argv に出さない
    env_file = _workspace_env_file(key)
    lines = [f"OPENCODE_PASSWORD={password}"]
    zen_file = sandbox.zen_env_file(box)
    if zen_file is not None:
        try:
            lines.append(zen_file.read_text(encoding="utf-8").strip())
        finally:
            zen_file.unlink(missing_ok=True)
    fd = os.open(env_file, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write("\n".join(lines) + "\n")
    os.chmod(env_file, 0o600)
    server = Server(key=key, port=port, password=password, unit=f"cd-opencode-chat-{key}",
                    config=str(config_dir), project=str(box.repo), permission_file=str(permission_file),
                    started_at=time.time(), last_used=time.time(), owner=owner_user_id, workspace=box.name)
    set_mode(server, str(provider.get_settings().get("chat_permission_mode") or "auto"))
    command = [str(binary), "serve", "--hostname", "127.0.0.1", "--port", str(port)]
    try:
        argv_podman = sandbox.podman_argv(
            box, command, name=f"{sandbox.CONTAINER_PREFIX}{box.name}-chat", env_file=env_file,
            env={"CONTROLDECK_PERMISSION_FILE": str(permission_file), "CONTROLDECK_ISOLATION": "box",
                 "CONTROL_DECK_OPENCODE_BOX": "1"},
            need_serve_port=True)
    except sandbox.SandboxError as exc:
        env_file.unlink(missing_ok=True)
        raise ChatServerError(str(exc)) from exc
    # 前回の残り（コンテナ名が同じだと起動できない）を先に消す
    _stop_unit(server.unit)
    podman = shutil.which("podman") or "podman"
    subprocess.run([podman, "rm", "-f", f"{sandbox.CONTAINER_PREFIX}{box.name}-chat"],
                   capture_output=True, timeout=60, check=False)
    argv = [systemd_run, "--user", "--quiet", "--collect", f"--unit={server.unit}",
            f"--working-directory={box.repo}", *argv_podman]
    result = subprocess.run(argv, capture_output=True, text=True, timeout=30, check=False)
    if result.returncode != 0:
        env_file.unlink(missing_ok=True)
        raise ChatServerError(f"OpenCode のサーバーを起動できませんでした（{result.stderr.strip()[:200]}）")
    _save(server)
    workspaces.touch(box)
    return server


async def ensure(project: Path, owner_user_id: int | None, *, network: str = "gateway") -> Server:
    """このプロジェクトのサーバーを返す。無ければ起こし、応答するまで待つ。

    プロジェクトが作業場（開発用の箱）なら、その箱の pod の中で起こす（持ち主だけ）。
    network は作業場の起動へ渡す（今は "gateway" だけ。秘密の会話用の "none" を段 2 で足す余地）。
    """
    key = server_key(project, owner_user_id)
    box = await asyncio.to_thread(workspace_box, project, owner_user_id)
    lock = _locks.setdefault(key, asyncio.Lock())
    async with lock:
        await asyncio.to_thread(reap_idle, keep=key)
        server = await asyncio.to_thread(load, key)
        if server is not None and await _healthy(server):
            await asyncio.to_thread(touch, server)
            if box is not None:
                from app.maintenance import workspaces

                await asyncio.to_thread(workspaces.touch, box)
            return server
        if server is not None:
            await asyncio.to_thread(stop, key)
        if box is not None:
            server = await asyncio.to_thread(_start_workspace, box, owner_user_id, key, network=network)
        else:
            server = await asyncio.to_thread(_start, project, owner_user_id, key)
        deadline = time.monotonic() + START_TIMEOUT
        while time.monotonic() < deadline:
            if await _healthy(server):
                return server
            if not await asyncio.to_thread(_unit_active, server.unit):
                break
            await asyncio.sleep(0.5)
        await asyncio.to_thread(stop, key)
        raise ChatServerError("OpenCode のサーバーが応答しませんでした")


async def resolve_project(project_name: str = "", project_path: str = "") -> Path:
    """チャットで指定されたプロジェクト（CodeDEV の名前・取り込むフォルダ・既定）を決める。"""
    from app.files import service as files
    from app.integrations.opencode import provider

    if project_name.strip():
        project = Path((await asyncio.to_thread(provider.ensure_project, project_name))["path"])
    elif project_path.strip():
        from app.maintenance import workspaces

        # 作業場（開発用の箱）の作業ツリーは取り込まずそのまま使う（持ち主の確認は ensure）
        direct = Path(project_path.strip()).expanduser()
        if await asyncio.to_thread(workspaces.box_for_project, direct) is not None:
            return await asyncio.to_thread(direct.resolve)
        imported = await asyncio.to_thread(provider.import_project, project_path)
        project = Path(imported["path"])
    else:
        raw = (await asyncio.to_thread(provider.get_settings)).get("project_path") or ""
        if not raw:
            raise ChatServerError("プロジェクトを指定してください")
        project = files.resolve(raw)
    if not await asyncio.to_thread(project.is_dir):
        raise ChatServerError("プロジェクトはディレクトリを指定してください")
    return await asyncio.to_thread(project.resolve)


# ---- API -----------------------------------------------------------------------------------

async def _call(server: Server, method: str, path: str, body: dict | None = None) -> Any:
    try:
        async with httpx.AsyncClient(timeout=30, trust_env=False) as client:
            response = await client.request(method, f"{server.base}{path}", headers=server.headers(),
                                             json=body)
    except httpx.HTTPError as exc:
        raise ChatServerError(f"OpenCode のサーバーにつながりません（{type(exc).__name__}）") from exc
    if response.status_code == 404:
        return None
    if response.status_code >= 400:
        raise ChatServerError(f"OpenCode の API がエラーを返しました（HTTP {response.status_code}）")
    if not response.content:
        return {}
    payload = response.json()
    return payload.get("data", payload) if isinstance(payload, dict) else payload


async def session_exists(server: Server, session_id: str) -> bool:
    if not session_id.startswith("ses_"):
        return False
    return bool(await _call(server, "GET", f"/api/session/{session_id}"))


async def create_session(server: Server, title: str) -> str:
    data = await _call(server, "POST", "/api/session",
                       {"title": title[:80] or "AI アシスタント", "location": {"directory": server.project}})
    session_id = str((data or {}).get("id") or "")
    if not session_id:
        raise ChatServerError("OpenCode のセッションを作れませんでした")
    return session_id


async def interrupt(server: Server, session_id: str) -> None:
    await _call(server, "POST", f"/api/session/{session_id}/interrupt", {})


async def reply_permission(server: Server, session_id: str, request_id: str, decision: str) -> None:
    if decision not in ("once", "always", "reject"):
        raise ChatServerError("許可の返答は once / always / reject のいずれかです")
    await _call(server, "POST", f"/api/session/{session_id}/permission/{request_id}/reply",
                {"decision": decision})


async def events(server: Server) -> AsyncIterator[dict]:
    """/api/event（SSE）のイベントを 1 件ずつ返す。"""
    timeout = httpx.Timeout(connect=10, read=None, write=10, pool=10)
    async with httpx.AsyncClient(timeout=timeout, trust_env=False) as client:
        async with client.stream("GET", f"{server.base}/api/event",
                                 headers={**server.headers(), "Accept": "text/event-stream"}) as response:
            if response.status_code != 200:
                raise ChatServerError(f"OpenCode のイベントを読めません（HTTP {response.status_code}）")
            async for line in response.aiter_lines():
                if line.startswith("data:"):
                    try:
                        yield json.loads(line[5:].strip())
                    except ValueError:
                        continue


def _tool_text(content: Any) -> str:
    parts = []
    for item in content or []:
        if isinstance(item, dict) and item.get("type") == "text":
            parts.append(str(item.get("text") or ""))
    text = "\n".join(parts)
    return text if len(text) <= TOOL_OUTPUT_LIMIT else text[:TOOL_OUTPUT_LIMIT] + "\n…（以下省略）"


Callback = Callable[[dict], Awaitable[None]]


async def _session_active(server: Server, session_id: str) -> bool:
    """このセッションがまだ処理中か（つなぎ直したあと、途中で終わっていないかを確かめる）。"""
    try:
        active = await _call(server, "GET", "/api/session/active")
    except ChatServerError:
        return True  # 分からなければ待ち続ける
    return session_id in json.dumps(active)


AGENTS = ("build", "plan")
FOLLOW_UP_SECONDS = 20.0   # 親のターンが終わってから、裏の子の結果で続きを始めるまで待つ長さ
BACKGROUND_CHECK_SECONDS = 15.0   # イベントを取りこぼしても止まらないよう、裏の子の状態を確かめる間隔
BACKGROUND_MAX_SECONDS = 4 * 3600.0   # 裏の子を待つ上限
_BACKGROUND_RE = re.compile(r"working in the background \(sessionID:\s*(ses_[A-Za-z0-9]+)\)")


def _background_child(tool: dict) -> str | None:
    if tool.get("name") not in ("subagent", "task"):
        return None
    found = _BACKGROUND_RE.search(str(tool.get("output") or ""))
    return found.group(1) if found else None


def _agent_model_ref(server: Server, agent: str) -> dict | None:
    """役割（エージェント）のモデル。設定の agents.<agent>.model、無ければ全体の model（`provider/モデル`）。

    OpenCode v2 は主のエージェントを切り替えても、セッションのモデルは変えない（2026-11-22 実測: プランに
    切り替えても既定のモデルのまま答えた）。役割ごとのモデル（計画＝別のモデルなど）を効かせるため、
    エージェントを切り替えるときにセッションのモデルも切り替える。子エージェントは設定のモデルが効く。"""
    base = Path(server.config)
    for path in (base / "opencode" / "opencode.json", base / "opencode.json"):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        agents = data.get("agents") or data.get("agent") or {}
        spec = agents.get(agent) if isinstance(agents, dict) else None
        value = str((spec.get("model") if isinstance(spec, dict) else "") or data.get("model") or "")
        provider_id, _, model_id = value.partition("/")
        return {"providerID": provider_id, "id": model_id} if provider_id and model_id else None
    return None


async def switch_model(server: Server, session_id: str, model: str) -> None:
    """このセッションのモデルを chain の次へ切り替える（使用量の上限で失敗したときに呼び出し元が使う）。

    モデルの参照は _agent_model_ref と同じ形（{"providerID", "id"}）。モデル名は `provider/モデル`。
    """
    provider_id, _, model_id = str(model).partition("/")
    if not provider_id or not model_id:
        raise ChatServerError(f"モデルの指定が正しくありません: {model}")
    if await _call(server, "POST", f"/api/session/{session_id}/model",
                   {"model": {"providerID": provider_id, "id": model_id}}) is None:
        raise ChatServerError("OpenCode のセッションが見つかりません")


async def switch_agent(server: Server, session_id: str, agent: str) -> None:
    """このセッションのエージェントを切り替える（build＝作る・直す、plan＝計画だけで何も変えない）。"""
    if agent not in AGENTS:
        raise ChatServerError(f"未対応のエージェントです: {agent}")
    if _session_agents.get((server.key, session_id)) == agent:
        return
    # 先に権限プラグインへ知らせる（切り替えの途中でも、プランの間は止める側に倒す）
    await asyncio.to_thread(mark_plan_session, server, session_id, agent == "plan")
    if await _call(server, "POST", f"/api/session/{session_id}/agent", {"agent": agent}) is None:
        raise ChatServerError("OpenCode のセッションが見つかりません")
    # v2 は名前を確かめずに 204 を返す（実測）。読み直して本当に切り替わったかを確かめる
    session = await _call(server, "GET", f"/api/session/{session_id}")
    if isinstance(session, dict) and session.get("agent") not in (None, agent):
        raise ChatServerError(f"OpenCode のエージェントを {agent} に切り替えられませんでした")
    # 役割のモデルへ切り替える（無ければそのまま）。失敗しても会話は止めない（今のモデルで続く）
    ref = await asyncio.to_thread(_agent_model_ref, server, agent)
    if ref and await _call(server, "POST", f"/api/session/{session_id}/model", {"model": ref}) is None:
        logger.warning("OpenCode のセッションのモデルを %s へ切り替えられませんでした", f"{ref['providerID']}/{ref['id']}")
    if len(_session_agents) > 1024:
        _session_agents.clear()
    _session_agents[(server.key, session_id)] = agent


async def session_is_active(server: Server, session_id: str) -> bool:
    return session_id in json.dumps(await _call(server, "GET", "/api/session/active") or [])


async def _required(server: Server, method: str, path: str, body: dict | None = None) -> Any:
    """見つからない（404）を成功として扱わない。"""
    result = await _call(server, method, path, body)
    if result is None:
        raise ChatServerError("OpenCode のセッションが見つかりません")
    return result


# 途中で伝えた要望が、まだ届いていないかもしれないセッション
_steered: set[str] = set()


async def _steer_pending(server: Server, session_id: str) -> bool:
    """最後の段の途中で伝えると、回の終わりの後に次の実行が始まる。それを待つか。"""
    if session_id not in _steered:
        return False
    _steered.discard(session_id)
    try:
        return bool(await _call(server, "GET", f"/api/session/{session_id}/inbox"))
    except ChatServerError:
        return False


async def steer_turn(server: Server, session_id: str, text: str) -> None:
    """作業中のセッションへ伝える。今の道具が終わったところでモデルに届く（実測 2026-11-18）。"""
    await _required(server, "POST", f"/api/session/{session_id}/prompt", {"text": text, "delivery": "steer"})
    _steered.add(session_id)


async def turn_changes(server: Server, session_id: str, message_id: str) -> list[dict]:
    """その依頼の回で変わったファイル（from だけ渡すと、その回の分が返る。実測 2026-11-18）。"""
    data = await _call(server, "GET", f"/api/session/{session_id}/diff?from={quote(message_id, safe='')}")
    return [item for item in (data or []) if isinstance(item, dict) and item.get("file")]


async def revert_turn(server: Server, session_id: str, message_id: str) -> None:
    """その依頼の前へ戻す（ファイルとセッションの両方）。確定に失敗したら元へ戻す。"""
    await _required(server, "POST", f"/api/session/{session_id}/revert/stage", {"messageID": message_id, "files": True})
    try:
        await _required(server, "POST", f"/api/session/{session_id}/revert/commit")
    except ChatServerError:
        await _call(server, "DELETE", f"/api/session/{session_id}/revert")
        raise


async def run_turn(server: Server, session_id: str, text: str, *,
                   on_text: Callable[[str], Awaitable[None]],
                   on_reasoning: Callable[[str], Awaitable[None]],
                   on_tool: Callback, on_permission: Callback, agent: str = "build",
                   on_status: Callback | None = None, files: list[dict] | None = None) -> dict:
    """1 回分の発言を送り、終わるまでイベントを呼び出し元へ渡す。中断されたら OpenCode も止める。

    子エージェント（このセッションから作られた別セッション）の許可の問い合わせも渡す
    （答えないと親の発言が止まり続けるため）。許可には sessionID を添える（返答先）。
    戻り値: {"status": "succeeded" | "failed" | "interrupted", "error": str}
    """
    queue: asyncio.Queue[dict | None] = asyncio.Queue()
    ready = asyncio.Event()

    async def reader() -> None:
        attempts = 0
        while attempts < 5:
            try:
                async for event in events(server):
                    if event.get("type") == "server.connected":
                        if attempts:
                            await queue.put({"type": "__reconnected"})
                        ready.set()
                        attempts = 0
                    await queue.put(event)
            except (httpx.HTTPError, ChatServerError) as exc:
                logger.warning("OpenCode のイベントが切れました（つなぎ直します）: %s", exc)
            attempts += 1
            ready.set()
            await asyncio.sleep(1)
        await queue.put(None)

    _active[server.key] = _active.get(server.key, 0) + 1
    task = asyncio.create_task(reader())
    tools: dict[str, dict] = {}
    family = {session_id}          # このセッションと、そこから作られた子
    last_touch = time.monotonic()
    # 裏で動く子エージェント（レビューなど）。親のターンは依頼した時点で一度終わり、子が終わると親が続きを動かす。
    # 親の 1 回目の終わりで聞くのをやめると、続き（レビューの結果）が画面に届かなかった（2026-11-18 本番）
    background: dict[str, str] = {}     # 子のセッション ID → 道具の ID
    follow_up_until = 0.0               # 親のターンの後、結果を受けた続きが始まるのを待つ期限
    follow_up_due = False               # 親がまだ受け取っていない子の完了がある
    ended_children: set[str] = set()    # 道具の結果より先に終わった子
    background_since = 0.0
    last_check = 0.0
    parent_running = False
    resumed = False
    needs_break = False
    phase = ""
    deltas: deque[float] = deque()
    last_status = 0.0
    total_deltas = 0

    async def status(next_phase: str, detail: str = "", *, force: bool = False) -> None:
        nonlocal phase, last_status
        now = time.monotonic()
        while deltas and now - deltas[0] > 4.0:
            deltas.popleft()
        if on_status is None or (next_phase == phase and not force and now - last_status < 1.0):
            return
        phase, last_status = next_phase, now
        await on_status({"phase": next_phase, "detail": detail, "tok_per_sec": round(len(deltas) / 4.0, 1),
                         "gen_tokens": total_deltas})

    def finished_waiting() -> bool:
        return not background and not parent_running and time.monotonic() >= follow_up_until

    async def child_ended(child: str, kind: str) -> None:
        nonlocal follow_up_due, follow_up_until
        tool = tools.get(background.pop(child, ""), None)
        if tool is not None:
            ok = kind.endswith("succeeded")
            tool["status"] = "completed" if ok else "failed"
            tool["output"] = "裏のエージェントが終わりました" if ok else "裏のエージェントが途中で止まりました"
            tool["ended_at"] = time.time()
            await on_tool(tool)
        follow_up_due = True
        if not parent_running:
            follow_up_until = time.monotonic() + FOLLOW_UP_SECONDS
            if not background:
                await status("waiting", "裏のエージェントの結果を受け取っています", force=True)

    async def reconcile() -> None:
        """イベントを取りこぼしても、実際に動いているセッションから状態を合わせる。"""
        nonlocal parent_running, follow_up_until, last_check
        last_check = time.monotonic()
        try:
            active = json.dumps(await _call(server, "GET", "/api/session/active"))
        except ChatServerError:
            return
        for child in list(background):
            if child not in active:
                await child_ended(child, "session.execution.succeeded")
        was_running, parent_running = parent_running, session_id in active
        if was_running and not parent_running and follow_up_due:
            follow_up_until = time.monotonic() + FOLLOW_UP_SECONDS

    async def stop_children(reason: str) -> None:
        for child, tool_id in list(background.items()):
            try:
                await interrupt(server, child)
            except ChatServerError:
                logger.warning("裏のエージェントの中断を伝えられませんでした")
            tool = tools.get(tool_id)
            if tool is not None:
                tool.update(status="failed", output=reason, ended_at=time.time())
                await on_tool(tool)
        background.clear()

    message_id = ""
    try:
        await asyncio.wait_for(ready.wait(), timeout=15)
        # 毎回そろえる（前の発言がプランでも、今回がビルドならビルドで動く）
        await switch_agent(server, session_id, agent)
        # 画像は data: の URI で発言に添付する（OpenCode が読み、画像を受け取れるモデルにそのまま渡す）
        prompted = await _call(server, "POST", f"/api/session/{session_id}/prompt",
                               {"text": text, **({"files": files} if files else {})})
        message_id = str((prompted or {}).get("id") or "")   # この回の変更を調べる・取り消す目印
        if message_id and on_status:                         # 止めた回でも変更を確かめられるよう、先に知らせる
            await on_status({"phase": "waiting", "message_id": message_id})
        parent_running = True
        await status("waiting", "モデルの応答を待っています", force=True)
        while True:
            waiting_background = not parent_running and bool(background or follow_up_until)
            try:
                event = await asyncio.wait_for(queue.get(), timeout=2.0 if waiting_background else TOUCH_SECONDS)
            except asyncio.TimeoutError:
                event = {"type": "__tick"}
            waiting_background = not parent_running and bool(background or follow_up_until)
            if waiting_background and event.get("type") != "__reconnected":
                if time.monotonic() - last_check >= BACKGROUND_CHECK_SECONDS:
                    await reconcile()
                if finished_waiting():
                    return {"status": "succeeded", "error": "", "message_id": message_id}
                if background and time.monotonic() - background_since > BACKGROUND_MAX_SECONDS:
                    await stop_children("待ち時間の上限で止めました")
                    return {"status": "succeeded", "error": "", "message_id": message_id}
            if time.monotonic() - last_touch >= TOUCH_SECONDS:
                last_touch = time.monotonic()
                await asyncio.to_thread(touch, server)   # 長い作業や許可待ちの間に掃除されないように
            if event is None:
                return {"status": "failed", "error": "OpenCode のイベントが途切れました", "message_id": message_id}
            kind = str(event.get("type") or "")
            if kind == "__reconnected":
                was_running = parent_running
                await reconcile()
                if was_running and not parent_running:
                    resumed = True
                if finished_waiting():
                    return {"status": "succeeded", "error": "", "message_id": message_id}  # 切れている間に終わっていた
                continue
            if kind == "__tick":
                continue
            data = event.get("data") if isinstance(event.get("data"), dict) else {}
            sid = data.get("sessionID")
            if kind == "session.created" and data.get("parentID") in family and data.get("id"):
                family.add(str(data["id"]))
                continue
            if sid != session_id and sid in family and kind in ("session.execution.succeeded", "session.execution.failed",
                                                                "session.execution.interrupted"):
                if sid in background:
                    await child_ended(str(sid), kind)
                else:
                    ended_children.add(str(sid))
                continue
            if kind.startswith("permission."):
                if sid not in family:
                    continue
            elif sid not in (None, session_id):
                continue  # 子エージェントの本文・道具は段階 2 で出す
            if kind == "session.execution.started":
                parent_running, follow_up_until, follow_up_due = True, 0.0, False
                needs_break = resumed
                await status("waiting", "モデルの応答を待っています", force=True)
            elif kind == "session.step.started":
                await status("prefill", "プロンプトを読み込んでいます", force=True)
            elif kind == "session.retry.scheduled":
                await status("retry", "再試行を待っています", force=True)
            elif kind == "session.text.delta":
                if needs_break:
                    needs_break = False
                    await on_text("\n\n")
                deltas.append(time.monotonic())
                total_deltas += 1
                await on_text(str(data.get("delta") or ""))
                await status("answer")
            elif kind == "session.reasoning.delta":
                deltas.append(time.monotonic())
                total_deltas += 1
                await on_reasoning(str(data.get("delta") or ""))
                await status("thinking")
            elif kind == "session.tool.input.started" and data.get("id"):
                tools[data["id"]] = {"id": data["id"], "name": data.get("name", ""), "status": "running",
                                     "input": {}, "output": "", "started_at": time.time()}
                await on_tool(tools[data["id"]])
                await status("tool", str(data.get("name") or ""), force=True)
            elif kind == "session.tool.called" and data.get("id") in tools:
                tools[data["id"]]["input"] = data.get("input") or {}
                await on_tool(tools[data["id"]])
            elif kind in ("session.tool.success", "session.tool.failed") and data.get("id") in tools:
                tool = tools[data["id"]]
                tool["status"] = "completed" if kind.endswith("success") else "failed"
                tool["output"] = _tool_text(data.get("content"))
                tool["ended_at"] = time.time()
                if kind.endswith("failed"):
                    tool["output"] = str((data.get("error") or {}).get("message") or "失敗しました")
                child = _background_child(tool) if kind.endswith("success") else None
                if child and child in ended_children:
                    follow_up_due = True                # 結果の知らせより先に終わっていた
                elif child:
                    family.add(child)
                    background[child] = tool["id"]
                    background_since = background_since or time.monotonic()
                    tool["status"] = "running"          # 裏で動いている間は実行中のまま見せる
                    tool.pop("ended_at", None)
                await on_tool(tool)
                await status("waiting", "モデルの応答を待っています", force=True)
            elif kind == "permission.asked" and data.get("id"):
                await on_permission({"id": data.get("id"), "sessionID": sid, "child": sid != session_id,
                                     "action": data.get("action"),
                                     "resources": list(data.get("resources") or []),
                                     "save": list(data.get("save") or []),
                                     "tool": (data.get("source") or {}).get("id"), "status": "pending"})
            elif kind == "permission.replied" and data.get("requestID"):
                await on_permission({"id": data.get("requestID"), "status": str(data.get("reply") or "")})
            elif kind == "session.execution.succeeded":
                parent_running = False
                if await _steer_pending(server, session_id):   # 伝えた要望の続きが始まるのを待つ
                    follow_up_due = True
                resumed = True
                if follow_up_due:                       # 結果を受けた続きは、親が終わってから始まる
                    follow_up_until = time.monotonic() + FOLLOW_UP_SECONDS
                if finished_waiting():
                    return {"status": "succeeded", "error": "", "message_id": message_id}
                await status("background", f"裏のエージェント {len(background)} 件の完了を待っています", force=True)
            elif kind == "session.execution.interrupted":
                await stop_children("親の作業を止めたので止めました")
                return {"status": "interrupted", "error": "", "message_id": message_id}
            elif kind == "session.execution.failed":
                error = data.get("error") if isinstance(data.get("error"), dict) else {}
                await stop_children("親の作業が失敗したので止めました")
                return {"status": "failed", "error": str(error.get("message") or "OpenCode の実行に失敗しました")[:300], "message_id": message_id}
    except asyncio.CancelledError:
        try:
            await asyncio.shield(interrupt(server, session_id))
        except ChatServerError:
            logger.warning("OpenCode の中断を伝えられませんでした")
        await asyncio.shield(stop_children("止めました"))
        raise
    except asyncio.TimeoutError as exc:
        raise ChatServerError("OpenCode のイベントにつながりませんでした") from exc
    finally:
        task.cancel()
        _active[server.key] = _active.get(server.key, 1) - 1
        if _active[server.key] <= 0:
            _active.pop(server.key, None)
        try:
            await asyncio.shield(asyncio.to_thread(touch, server))
        except Exception:  # noqa: BLE001 - 使った時刻の記録に失敗しても会話は終える
            logger.warning("OpenCode のサーバーの使用時刻を記録できませんでした")
