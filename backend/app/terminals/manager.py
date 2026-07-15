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
import re
import secrets
import select
import shutil
import signal
import struct
import subprocess
import termios
import time
from dataclasses import dataclass, field

from app.config import data_dir, get_config

TMUX_PREFIX = "cdterm-"
SESSION_ID_RE = re.compile(r"^[0-9a-f]{8}$")
HISTORY_LINES = 100_000
HISTORY_BYTES = 16 * 1024 * 1024
HISTORY_TRUNCATED = b"\r\n\x1b[33m[Control Deck: history older than 16 MiB was truncated]\x1b[0m\r\n"


def tmux_available() -> bool:
    return shutil.which("tmux") is not None


def _set_winsize(fd: int, rows: int, cols: int) -> None:
    fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))


def _target(sid: str) -> str:
    if not SESSION_ID_RE.fullmatch(sid):
        raise KeyError("セッションが見つかりません")
    return TMUX_PREFIX + sid


def _bounded_history(data: bytes) -> tuple[bytes, bool]:
    if len(data) <= HISTORY_BYTES:
        return data, False
    cut = len(data) - HISTORY_BYTES
    newline = data.find(b"\n", cut, min(len(data), cut + 65_536))
    if newline >= 0:
        cut = newline + 1
    return HISTORY_TRUNCATED + data[cut:], True


def _tmux_config_path() -> str:
    path = data_dir() / "terminal-tmux.conf"
    content = f"set -g history-limit {HISTORY_LINES}\n"
    try:
        if not path.exists() or path.read_text(encoding="utf-8") != content:
            path.write_text(content, encoding="utf-8")
        path.chmod(0o600)
    except OSError as exc:
        raise RuntimeError(f"tmux履歴設定を保存できません: {exc}") from exc
    return str(path.resolve())


def _initial_pty_output(fd: int) -> bytes:
    """tmux attach直後の端末初期化/全画面描画を履歴snapshotより先に処理する。"""
    chunks: list[bytes] = []
    deadline = time.monotonic() + 0.75
    received = False
    while time.monotonic() < deadline:
        ready, _, _ = select.select([fd], [], [], 0.05)
        if not ready:
            if received:
                break
            continue
        try:
            data = os.read(fd, 65_536)
        except (BlockingIOError, OSError):
            break
        if not data:
            break
        chunks.append(data)
        received = True
        if sum(map(len, chunks)) >= 1_048_576:
            break
    return b"".join(chunks)


@dataclass
class PtySession:
    """プロセス内 PTY セッション（tmux なしフォールバック用）。"""

    id: str
    name: str
    master_fd: int
    pid: int
    created_at: float = field(default_factory=time.time)
    buffer: bytearray = field(default_factory=bytearray)  # 再接続時のリプレイ用
    buffer_truncated: bool = False
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

    def create_session(self, cwd: str | None = None, command: str | None = None) -> dict:
        """セッションを作成する。command 指定時はシェルでそのコマンドを実行する
        （例: gh auth login。継続利用するコマンド側で exec bash 等を付ける）。"""
        cfg = get_config().terminal
        if not cfg.enabled:
            raise RuntimeError("ターミナルは無効化されています")
        if len(self.list_sessions()) >= cfg.max_sessions:
            raise RuntimeError(f"セッション数の上限（{cfg.max_sessions}）に達しています")
        sid = secrets.token_hex(4)
        workdir = cwd or str(os.path.expanduser("~"))
        if tmux_available():
            name = TMUX_PREFIX + sid
            config_path = _tmux_config_path()
            # 既存tmux serverには次に作るpane用のglobal値を先に反映する。
            subprocess.run(
                ["tmux", "set-option", "-g", "history-limit", str(HISTORY_LINES)],
                capture_output=True, timeout=10,
            )
            # tmux はコマンドを 1 引数で渡すと sh -c で実行する（複数引数は空白結合されるため不可）
            base = ["tmux", "-f", config_path, "new-session", "-d", "-s", name, "-c", workdir,
                    command or cfg.shell]
            # tmux サーバーを本サービスの cgroup 外（独立 scope）で起動する。
            # そうしないとサービス再起動時に systemd が cgroup ごと tmux を kill し、
            # 「永続」のはずのセッションが全て消える。
            if shutil.which("systemd-run"):
                r = subprocess.run(
                    ["systemd-run", "--user", "--collect", "--scope", "--"] + base,
                    capture_output=True, text=True, timeout=10,
                )
                if r.returncode != 0:  # systemd-run 不可の環境（コンテナ等）は直接起動
                    r = subprocess.run(base, capture_output=True, text=True, timeout=10)
            else:
                r = subprocess.run(base, capture_output=True, text=True, timeout=10)
            if r.returncode != 0:
                raise RuntimeError(f"tmux セッション作成に失敗: {r.stderr.strip()}")
            # tmuxの既定status bar（緑色）は、モバイルでソフトキーボード表示時に
            # 入力欄のように見えるうえ表示領域を1行消費する。セッション切替はWeb UI側に
            # あるためControl Deckセッションだけ非表示にする（他のtmux sessionへ影響させない）。
            subprocess.run(
                ["tmux", "set-option", "-t", name, "status", "off"],
                capture_output=True, timeout=10,
            )
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
            ["bash", "-lc", command] if command else [cfg.shell, "-l"],
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
                ["tmux", "kill-session", "-t", _target(sid)],
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
            target = _target(sid)
            exists = subprocess.run(
                ["tmux", "has-session", "-t", target], capture_output=True, timeout=10,
            )
            if exists.returncode != 0:
                raise KeyError("セッションが見つかりません")
            # 改修前から残る永続sessionにも接続時に同じ表示設定を適用する。
            subprocess.run(
                ["tmux", "set-option", "-t", target, "status", "off"],
                capture_output=True, timeout=10,
            )
            master, slave = pty.openpty()
            env = {
                "TERM": "xterm-256color",
                "HOME": os.path.expanduser("~"),
                "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
                "LANG": os.environ.get("LANG", "C.UTF-8"),
            }
            proc = subprocess.Popen(
                ["tmux", "attach-session", "-t", target],
                stdin=slave, stdout=slave, stderr=slave,
                env=env, start_new_session=True, close_fds=True,
            )
            os.close(slave)
            os.set_blocking(master, False)
            _set_winsize(master, rows, cols)
            initial = _initial_pty_output(master)
            # attach初期描画をdrainした後にcaptureする。capture後の出力はPTYへ流れるため、
            # 接続確立中に生成された行もsnapshot/通常streamのどちらかへ必ず入る。
            captured = subprocess.run(
                ["tmux", "capture-pane", "-p", "-e", "-S", "-", "-t", target],
                capture_output=True, timeout=15,
            )
            if captured.returncode != 0:
                os.close(master)
                os.killpg(os.getpgid(proc.pid), signal.SIGHUP)
                raise KeyError("セッションが見つかりません")
            replay, _ = _bounded_history(captured.stdout.replace(b"\n", b"\r\n"))
            return TerminalConnection(master_fd=master, pid=proc.pid, owns_process=True,
                                      replay=replay, initial=initial)
        s = self._fallback.get(sid)
        if s is None or not s.alive():
            raise KeyError("セッションが見つかりません")
        _set_winsize(s.master_fd, rows, cols)
        s.attached = True
        return TerminalConnection(
            master_fd=s.master_fd, pid=s.pid, owns_process=False,
            replay=(HISTORY_TRUNCATED if s.buffer_truncated else b"") + bytes(s.buffer), session=s,
        )


class TerminalConnection:
    """1 つの WS 接続と PTY の橋渡し。"""

    def __init__(
        self,
        master_fd: int,
        pid: int,
        owns_process: bool,
        replay: bytes = b"",
        initial: bytes = b"",
        session: PtySession | None = None,
    ) -> None:
        self.master_fd = master_fd
        self.pid = pid
        self.owns_process = owns_process  # True: 切断時に attach プロセスを終了（tmux 側は継続）
        self.replay = replay
        self.initial = initial
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
                    if len(self.session.buffer) > HISTORY_BYTES:
                        overflow = len(self.session.buffer) - HISTORY_BYTES
                        newline = self.session.buffer.find(b"\n", overflow, min(len(self.session.buffer), overflow + 65_536))
                        del self.session.buffer[:newline + 1 if newline >= 0 else overflow]
                        self.session.buffer_truncated = True
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
