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


async def ensure(project: Path, owner_user_id: int | None) -> Server:
    """このプロジェクトのサーバーを返す。無ければ起こし、応答するまで待つ。"""
    key = server_key(project, owner_user_id)
    lock = _locks.setdefault(key, asyncio.Lock())
    async with lock:
        await asyncio.to_thread(reap_idle, keep=key)
        server = await asyncio.to_thread(load, key)
        if server is not None and await _healthy(server):
            await asyncio.to_thread(touch, server)
            return server
        if server is not None:
            await asyncio.to_thread(stop, key)
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
FOLLOW_UP_SECONDS = 20.0   # 裏の子が終わってから、親が続きを始めるまで待つ長さ
_BACKGROUND_RE = re.compile(r"working in the background \(sessionID:\s*(ses_[A-Za-z0-9]+)\)")


def _background_child(tool: dict) -> str | None:
    if tool.get("name") not in ("subagent", "task"):
        return None
    found = _BACKGROUND_RE.search(str(tool.get("output") or ""))
    return found.group(1) if found else None


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
    if len(_session_agents) > 1024:
        _session_agents.clear()
    _session_agents[(server.key, session_id)] = agent


async def run_turn(server: Server, session_id: str, text: str, *,
                   on_text: Callable[[str], Awaitable[None]],
                   on_reasoning: Callable[[str], Awaitable[None]],
                   on_tool: Callback, on_permission: Callback, agent: str = "build",
                   on_status: Callback | None = None) -> dict:
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
    follow_up_until = 0.0               # 子が終わった後、親が続きを始めるのを待つ期限
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

    try:
        await asyncio.wait_for(ready.wait(), timeout=15)
        # 毎回そろえる（前の発言がプランでも、今回がビルドならビルドで動く）
        await switch_agent(server, session_id, agent)
        await _call(server, "POST", f"/api/session/{session_id}/prompt", {"text": text})
        parent_running = True
        await status("waiting", "モデルの応答を待っています", force=True)
        while True:
            waiting_background = not parent_running and (background or follow_up_until)
            try:
                event = await asyncio.wait_for(queue.get(), timeout=2.0 if waiting_background else TOUCH_SECONDS)
            except asyncio.TimeoutError:
                event = {"type": "__tick"}
            if time.monotonic() - last_touch >= TOUCH_SECONDS:
                last_touch = time.monotonic()
                await asyncio.to_thread(touch, server)   # 長い作業や許可待ちの間に掃除されないように
            if event is None:
                return {"status": "failed", "error": "OpenCode のイベントが途切れました"}
            kind = str(event.get("type") or "")
            if kind == "__reconnected":
                if not await _session_active(server, session_id):
                    return {"status": "succeeded", "error": ""}  # 切れている間に終わっていた
                continue
            if kind == "__tick":
                if waiting_background and finished_waiting():
                    return {"status": "succeeded", "error": ""}
                continue
            data = event.get("data") if isinstance(event.get("data"), dict) else {}
            sid = data.get("sessionID")
            if kind == "session.created" and data.get("parentID") in family and data.get("id"):
                family.add(str(data["id"]))
                continue
            if sid in background and kind in ("session.execution.succeeded", "session.execution.failed",
                                              "session.execution.interrupted"):
                tool = tools.get(background.pop(str(sid)), None)
                if tool is not None:
                    tool["status"] = "failed" if kind.endswith("failed") else "completed"
                    tool["output"] = "裏のエージェントが終わりました"
                    tool["ended_at"] = time.time()
                    await on_tool(tool)
                follow_up_until = time.monotonic() + FOLLOW_UP_SECONDS
                if not parent_running and not background:
                    await status("waiting", "裏のエージェントの結果を受け取っています", force=True)
                continue
            if kind.startswith("permission."):
                if sid not in family:
                    continue
            elif sid not in (None, session_id):
                continue  # 子エージェントの本文・道具は段階 2 で出す
            if kind == "session.execution.started":
                parent_running, follow_up_until = True, 0.0
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
                if child and child in family:
                    background[child] = tool["id"]
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
                resumed = True
                if finished_waiting():
                    return {"status": "succeeded", "error": ""}
                await status("background", f"裏のエージェント {len(background)} 件の完了を待っています", force=True)
            elif kind == "session.execution.interrupted":
                return {"status": "interrupted", "error": ""}
            elif kind == "session.execution.failed":
                error = data.get("error") if isinstance(data.get("error"), dict) else {}
                return {"status": "failed", "error": str(error.get("message") or "OpenCode の実行に失敗しました")[:300]}
    except asyncio.CancelledError:
        try:
            await asyncio.shield(interrupt(server, session_id))
            for child in list(background):
                await asyncio.shield(interrupt(server, child))
        except ChatServerError:
            logger.warning("OpenCode の中断を伝えられませんでした")
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
