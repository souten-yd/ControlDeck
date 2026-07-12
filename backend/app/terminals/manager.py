"""Web ターミナルの PTY セッション管理。

- tmux があれば tmux セッション（cdterm-*）として作成し、Web/バックエンド再起動後も継続する。
  WS 接続ごとに `tmux attach` を PTY 内で起動して橋渡しする。
- tmux がなければプロセス内 PTY（bash）で代替する。WS 切断後もプロセスは維持され
  再接続できるが、バックエンド再起動では失われる。
"""
from __future__ import annotations

import asyncio
import fcntl
import os
import pty
import secrets
import shutil
import signal
import struct
import subprocess
import termios
import time
from dataclasses import dataclass, field

from app.config import get_config

TMUX_PREFIX = "cdterm-"


def tmux_available() -> bool:
    return shutil.which("tmux") is not None


def _set_winsize(fd: int, rows: int, cols: int) -> None:
    fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))


@dataclass
class PtySession:
    """プロセス内 PTY セッション（tmux なしフォールバック用）。"""

    id: str
    name: str
    master_fd: int
    pid: int
    created_at: float = field(default_factory=time.time)
    buffer: bytearray = field(default_factory=bytearray)  # 再接続時のリプレイ用
    attached: bool = False

    def alive(self) -> bool:
        try:
            os.kill(self.pid, 0)
            return True
        except OSError:
            return False


class TerminalManager:
    def __init__(self) -> None:
        self._fallback: dict[str, PtySession] = {}

    # ---- セッション一覧・作成・削除 ----

    def list_sessions(self) -> list[dict]:
        if tmux_available():
            r = subprocess.run(
                ["tmux", "list-sessions", "-F", "#{session_name}\t#{session_created}\t#{session_attached}"],
                capture_output=True, text=True, timeout=10,
            )
            sessions = []
            if r.returncode == 0:
                for line in r.stdout.splitlines():
                    parts = line.split("\t")
                    if not parts[0].startswith(TMUX_PREFIX):
                        continue
                    sessions.append(
                        {
                            "id": parts[0][len(TMUX_PREFIX):],
                            "name": parts[0],
                            "created_at": float(parts[1]) if len(parts) > 1 else 0,
                            "attached": parts[2] == "1" if len(parts) > 2 else False,
                            "persistent": True,
                        }
                    )
            return sessions
        # フォールバック
        dead = [sid for sid, s in self._fallback.items() if not s.alive()]
        for sid in dead:
            self._close_fallback(sid)
        return [
            {
                "id": s.id,
                "name": s.name,
                "created_at": s.created_at,
                "attached": s.attached,
                "persistent": False,
            }
            for s in self._fallback.values()
        ]

    def create_session(self, cwd: str | None = None) -> dict:
        cfg = get_config().terminal
        if not cfg.enabled:
            raise RuntimeError("ターミナルは無効化されています")
        if len(self.list_sessions()) >= cfg.max_sessions:
            raise RuntimeError(f"セッション数の上限（{cfg.max_sessions}）に達しています")
        sid = secrets.token_hex(4)
        workdir = cwd or str(os.path.expanduser("~"))
        if tmux_available():
            name = TMUX_PREFIX + sid
            r = subprocess.run(
                ["tmux", "new-session", "-d", "-s", name, "-c", workdir, cfg.shell],
                capture_output=True, text=True, timeout=10,
            )
            if r.returncode != 0:
                raise RuntimeError(f"tmux セッション作成に失敗: {r.stderr.strip()}")
            return {"id": sid, "name": name, "persistent": True}
        # フォールバック: プロセス内 PTY
        master, slave = pty.openpty()
        env = {
            "TERM": "xterm-256color",
            "HOME": os.path.expanduser("~"),
            "USER": os.environ.get("USER", ""),
            "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
            "LANG": os.environ.get("LANG", "C.UTF-8"),
        }
        proc = subprocess.Popen(
            [cfg.shell, "-l"],
            stdin=slave, stdout=slave, stderr=slave,
            cwd=workdir, env=env, start_new_session=True, close_fds=True,
        )
        os.close(slave)
        os.set_blocking(master, False)
        session = PtySession(id=sid, name=f"pty-{sid}", master_fd=master, pid=proc.pid)
        self._fallback[sid] = session
        return {"id": sid, "name": session.name, "persistent": False}

    def kill_session(self, sid: str) -> None:
        if tmux_available():
            subprocess.run(
                ["tmux", "kill-session", "-t", TMUX_PREFIX + sid],
                capture_output=True, timeout=10,
            )
            return
        self._close_fallback(sid, kill=True)

    def _close_fallback(self, sid: str, kill: bool = False) -> None:
        s = self._fallback.pop(sid, None)
        if s is None:
            return
        if kill:
            try:
                os.killpg(os.getpgid(s.pid), signal.SIGHUP)
            except OSError:
                pass
        try:
            os.close(s.master_fd)
        except OSError:
            pass

    # ---- WS ブリッジ用の接続確立 ----

    def open_connection(self, sid: str, rows: int, cols: int) -> "TerminalConnection":
        if tmux_available():
            master, slave = pty.openpty()
            env = {
                "TERM": "xterm-256color",
                "HOME": os.path.expanduser("~"),
                "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
                "LANG": os.environ.get("LANG", "C.UTF-8"),
            }
            proc = subprocess.Popen(
                ["tmux", "attach-session", "-t", TMUX_PREFIX + sid],
                stdin=slave, stdout=slave, stderr=slave,
                env=env, start_new_session=True, close_fds=True,
            )
            os.close(slave)
            os.set_blocking(master, False)
            _set_winsize(master, rows, cols)
            return TerminalConnection(master_fd=master, pid=proc.pid, owns_process=True)
        s = self._fallback.get(sid)
        if s is None or not s.alive():
            raise KeyError("セッションが見つかりません")
        _set_winsize(s.master_fd, rows, cols)
        s.attached = True
        return TerminalConnection(
            master_fd=s.master_fd, pid=s.pid, owns_process=False,
            replay=bytes(s.buffer), session=s,
        )


class TerminalConnection:
    """1 つの WS 接続と PTY の橋渡し。"""

    def __init__(
        self,
        master_fd: int,
        pid: int,
        owns_process: bool,
        replay: bytes = b"",
        session: PtySession | None = None,
    ) -> None:
        self.master_fd = master_fd
        self.pid = pid
        self.owns_process = owns_process  # True: 切断時に attach プロセスを終了（tmux 側は継続）
        self.replay = replay
        self.session = session

    def write(self, data: bytes) -> None:
        os.write(self.master_fd, data)

    def resize(self, rows: int, cols: int) -> None:
        _set_winsize(self.master_fd, max(2, min(rows, 500)), max(2, min(cols, 1000)))

    async def read_loop(self, on_data) -> None:
        """PTY からの出力を非同期で on_data(bytes) へ渡す。EOF で終了。"""
        loop = asyncio.get_running_loop()
        queue: asyncio.Queue[bytes | None] = asyncio.Queue(maxsize=256)

        def _reader() -> None:
            try:
                data = os.read(self.master_fd, 65536)
            except BlockingIOError:
                return
            except OSError:
                data = b""
            if data:
                if self.session is not None:
                    self.session.buffer.extend(data)
                    if len(self.session.buffer) > 200_000:
                        del self.session.buffer[:-100_000]
                try:
                    queue.put_nowait(data)
                except asyncio.QueueFull:
                    pass
            else:
                loop.remove_reader(self.master_fd)
                queue.put_nowait(None)

        loop.add_reader(self.master_fd, _reader)
        try:
            while True:
                data = await queue.get()
                if data is None:
                    break
                await on_data(data)
        finally:
            try:
                loop.remove_reader(self.master_fd)
            except (OSError, ValueError):
                pass

    def close(self) -> None:
        if self.session is not None:
            self.session.attached = False
        if self.owns_process:
            try:
                os.kill(self.pid, signal.SIGHUP)
            except OSError:
                pass
            try:
                os.close(self.master_fd)
            except OSError:
                pass


manager = TerminalManager()
