"""インラインコードのテスト実行（WebSocket ストリーミング）。

FrameDeck のように起動後も動き続けるアプリを想定し、出力をリアルタイムに
中継する。クライアントの「停止」指示か WS 切断でプロセスを終了する。
"""
from __future__ import annotations

import asyncio
import json
import os
import signal
import tempfile
from pathlib import Path

MAX_RUNTIME_SECONDS = 30 * 60  # 暴走対策の上限（通常は停止ボタン/切断で終わる）
MAX_RELAY_BYTES = 1_000_000  # クライアントへ中継する出力の上限


class TestRunError(Exception):
    pass


def prepare(application_type: str, python_path: str | None, code: str,
            working_directory: str | None) -> tuple[list[str], str, str]:
    """検証して (argv, 一時ファイルパス, cwd) を返す。"""
    if application_type not in ("python_script", "shell_script"):
        raise TestRunError("コード実行は Python / シェルのみ対応です")
    if application_type == "python_script":
        py = python_path or "/usr/bin/python3"
        if not Path(py).is_file():
            raise TestRunError(f"Python 実行ファイルが見つかりません: {py}")
    # 実行時のカレントディレクトリは既定でホーム（明示指定があればそちら）
    cwd = Path(working_directory).expanduser() if working_directory else Path.home()
    if not cwd.is_dir():
        raise TestRunError(f"作業ディレクトリが見つかりません: {cwd}")
    suffix = ".py" if application_type == "python_script" else ".sh"
    with tempfile.NamedTemporaryFile("w", suffix=suffix, delete=False, encoding="utf-8") as f:
        f.write(code)
        tmp = f.name
    argv = (
        [python_path or "/usr/bin/python3", "-u", tmp]
        if application_type == "python_script"
        else ["/bin/bash", tmp]
    )
    return argv, tmp, str(cwd)


async def stream_run(websocket, argv: list[str], tmp: str, cwd: str) -> None:
    """プロセスを起動し、stdout/stderr を WS へ中継する。stop 指示/切断で終了。"""
    from starlette.websockets import WebSocketDisconnect

    send_lock = asyncio.Lock()

    async def send(obj: dict) -> None:
        async with send_lock:
            await websocket.send_text(json.dumps(obj, ensure_ascii=False))

    try:
        proc = await asyncio.create_subprocess_exec(
            *argv,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=cwd,
            start_new_session=True,  # killpg で子プロセスごと止めるため
        )
    except OSError as e:
        await send({"type": "error", "message": f"起動に失敗しました: {e}"})
        Path(tmp).unlink(missing_ok=True)
        return

    await send({"type": "start", "cwd": cwd})
    relayed = 0
    truncated = False

    def _terminate(sig: int = signal.SIGTERM) -> None:
        try:
            os.killpg(os.getpgid(proc.pid), sig)
        except (ProcessLookupError, OSError):
            pass

    async def relay(reader: asyncio.StreamReader, kind: str) -> None:
        nonlocal relayed, truncated
        while True:
            chunk = await reader.read(8192)
            if not chunk:
                break
            if relayed >= MAX_RELAY_BYTES:
                if not truncated:
                    truncated = True
                    await send({"type": "notice", "message": "出力が上限に達したため以降は省略します"})
                continue  # プロセスは動かし続けるが中継しない（PIPE は読み捨てて詰まり防止）
            relayed += len(chunk)
            await send({"type": kind, "data": chunk.decode("utf-8", errors="replace")})

    async def watch_client() -> None:
        """クライアントからの stop 指示を待つ。切断されたら終了させる。"""
        try:
            while True:
                msg = await websocket.receive_text()
                try:
                    ctrl = json.loads(msg)
                except json.JSONDecodeError:
                    continue
                if ctrl.get("type") == "stop":
                    _terminate()
                    return
        except (WebSocketDisconnect, RuntimeError):
            _terminate()

    watcher = asyncio.create_task(watch_client())
    try:
        try:
            await asyncio.wait_for(
                asyncio.gather(relay(proc.stdout, "stdout"), relay(proc.stderr, "stderr")),
                timeout=MAX_RUNTIME_SECONDS,
            )
        except asyncio.TimeoutError:
            _terminate()
            await send({"type": "notice", "message": "実行時間が上限に達したため停止しました"})
        # 終了待ち（SIGTERM が効かない場合は 3 秒で SIGKILL）
        try:
            code = await asyncio.wait_for(proc.wait(), timeout=3)
        except asyncio.TimeoutError:
            _terminate(signal.SIGKILL)
            code = await proc.wait()
        try:
            await send({"type": "exit", "code": code})
        except RuntimeError:
            pass  # クライアント切断済み
    finally:
        watcher.cancel()
        if proc.returncode is None:
            _terminate(signal.SIGKILL)
        Path(tmp).unlink(missing_ok=True)
