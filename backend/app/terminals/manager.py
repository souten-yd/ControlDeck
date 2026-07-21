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
from pathlib import Path

from app.config import data_dir, get_config

TMUX_PREFIX = "cdterm-"
TERMINAL_ENGINES = {"v1", "v2-lab"}
SESSION_ID_RE = re.compile(r"^[0-9a-f]{8}$")
HISTORY_LINES = 100_000
# tmux側の全履歴は保持しつつ、Web初期表示は最新部分だけを取得する。
# 4 MiBの一括解析ではxterm parser/rendererがモバイルのメインスレッドを
# 数秒占有するため、復元用は最大10,000行かつ512 KiBの小さい方に限定する。
HISTORY_REPLAY_LINES = 10_000
HISTORY_BYTES = 512 * 1024
HISTORY_TRUNCATED = b"\r\n\x1b[33m[Control Deck: Web restore shows recent history; older history remains in tmux]\x1b[0m\r\n"
IDLE_COMMANDS = {"bash", "dash", "fish", "sh", "tmux", "zsh"}


def tmux_available() -> bool:
    return shutil.which("tmux") is not None


def _set_winsize(fd: int, rows: int, cols: int) -> None:
    fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))


def _get_winsize(fd: int) -> tuple[int, int]:
    packed = fcntl.ioctl(fd, termios.TIOCGWINSZ, struct.pack("HHHH", 0, 0, 0, 0))
    rows, cols, _, _ = struct.unpack("HHHH", packed)
    return rows, cols


def _normalize_terminal_size(rows: int, cols: int) -> tuple[int, int]:
    """PTYへ適用できる実用範囲へ正規化する（rows, cols）。"""
    return max(3, min(rows, 500)), max(10, min(cols, 1000))


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


def _display_path(value: str) -> str:
    """認証済みUI用にhomeだけを短縮する。実パスはプロセス起動へ再利用しない。"""
    if not value:
        return "N/A"
    home = os.path.expanduser("~")
    if value == home:
        return "~"
    if value.startswith(home + os.sep):
        return "~" + value[len(home):]
    return value


def _session_metadata(*, command: str, cwd: str, pid: int, activity_at: float, alive: bool = True) -> dict[str, object]:
    program = os.path.basename(command.strip()) or "shell"
    return {
        "program": program,
        "cwd": _display_path(cwd),
        "pid": pid,
        "activity_at": activity_at,
        "alive": alive,
        "workload": "idle" if program in IDLE_COMMANDS else "running",
    }


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


def _initial_pty_output(
    fd: int,
    *,
    first_timeout: float = 0.75,
    quiet_timeout: float = 0.05,
) -> bytes:
    """tmux attach初期化出力を、最初の無出力区間まで有界にdrainする。"""
    chunks: list[bytes] = []
    total = 0
    deadline = time.monotonic() + first_timeout
    received = False
    while time.monotonic() < deadline:
        timeout = quiet_timeout if received else max(0.0, deadline - time.monotonic())
        ready, _, _ = select.select([fd], [], [], timeout)
        if not ready:
            if received:
                break
            break
        try:
            data = os.read(fd, 65_536)
        except (BlockingIOError, OSError):
            break
        if not data:
            break
        chunks.append(data)
        total += len(data)
        received = True
        if total >= 1_048_576:
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
    activity_at: float = field(default_factory=time.time)
    buffer: bytearray = field(default_factory=bytearray)  # 再接続時のリプレイ用
    buffer_truncated: bool = False
    attached: bool = False
    engine: str = "v1"

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
                ["tmux", "list-sessions", "-F",
                 "#{session_name}\t#{session_created}\t#{session_attached}\t#{session_activity}"
                 "\t#{pane_current_command}\t#{pane_current_path}\t#{pane_pid}\t#{pane_dead}"
                 "\t#{@control-deck-engine}"],
                capture_output=True, text=True, timeout=10,
            )
            sessions = []
            if r.returncode == 0:
                for line in r.stdout.splitlines():
                    parts = line.split("\t")
                    if not parts[0].startswith(TMUX_PREFIX):
                        continue
                    try:
                        pid = int(parts[6]) if len(parts) > 6 else 0
                    except ValueError:
                        pid = 0
                    sessions.append({
                        "id": parts[0][len(TMUX_PREFIX):],
                        "name": parts[0],
                        "created_at": float(parts[1]) if len(parts) > 1 else 0,
                        "attached": parts[2] == "1" if len(parts) > 2 else False,
                        "persistent": True,
                        "engine": parts[8] if len(parts) > 8 and parts[8] in TERMINAL_ENGINES else "v1",
                        **_session_metadata(
                            command=parts[4] if len(parts) > 4 else "",
                            cwd=parts[5] if len(parts) > 5 else "",
                            pid=pid,
                            activity_at=float(parts[3]) if len(parts) > 3 and parts[3] else 0,
                            alive=not (len(parts) > 7 and parts[7] == "1"),
                        ),
                    })
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
                "engine": s.engine,
                **self._fallback_metadata(s),
            }
            for s in self._fallback.values()
        ]

    @staticmethod
    def _fallback_metadata(session: PtySession) -> dict[str, object]:
        command = ""
        cwd = ""
        try:
            proc_root = Path("/proc") / str(session.pid)
            command = (proc_root / "comm").read_text(encoding="utf-8").strip()
            cwd = os.readlink(proc_root / "cwd")
        except OSError:
            pass
        return _session_metadata(
            command=command, cwd=cwd, pid=session.pid,
            activity_at=session.activity_at, alive=session.alive(),
        )

    def create_session(
        self, cwd: str | None = None, command: str | None = None, *, engine: str = "v1",
    ) -> dict:
        """セッションを作成する。command 指定時はシェルでそのコマンドを実行する
        （例: gh auth login。継続利用するコマンド側で exec bash 等を付ける）。"""
        cfg = get_config().terminal
        if engine not in TERMINAL_ENGINES:
            raise ValueError("unsupported terminal engine")
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
            tagged = subprocess.run(
                ["tmux", "set-option", "-t", name, "@control-deck-engine", engine],
                capture_output=True, timeout=10,
            )
            if tagged.returncode != 0:
                subprocess.run(["tmux", "kill-session", "-t", name], capture_output=True, timeout=10)
                raise RuntimeError("Terminal engine属性を保存できません")
            return {"id": sid, "name": name, "persistent": True, "engine": engine}
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
        session = PtySession(id=sid, name=f"pty-{sid}", master_fd=master, pid=proc.pid, engine=engine)
        self._fallback[sid] = session
        return {"id": sid, "name": session.name, "persistent": False, "engine": engine}

    def session_engine(self, sid: str) -> str:
        """Sessionへ永続化した描画engineを返す。未知Sessionはfail-closed。"""
        if not SESSION_ID_RE.fullmatch(sid):
            raise KeyError("セッションが見つかりません")
        session = next((item for item in self.list_sessions() if item.get("id") == sid), None)
        if session is None:
            raise KeyError("セッションが見つかりません")
        engine = str(session.get("engine") or "v1")
        return engine if engine in TERMINAL_ENGINES else "v1"

    def kill_session(self, sid: str) -> None:
        if tmux_available():
            subprocess.run(
                ["tmux", "kill-session", "-t", _target(sid)],
                capture_output=True, timeout=10,
            )
            return
        self._close_fallback(sid, kill=True)

    def inject_input(self, sid: str, text: str, *, submit: bool = True) -> int:
        """Bracket-paste an explicit automation payload into one exact session.

        The payload is provided to tmux over stdin, so code or prompt text is
        never exposed in a process argument. Callers must check their session
        precondition immediately before this operation.
        """
        payload = text.encode("utf-8")
        if not payload or len(payload) > 256 * 1024 or b"\x00" in payload:
            raise ValueError("Terminal input must be 1..262144 bytes without NUL")
        if tmux_available():
            target = _target(sid)
            exists = subprocess.run(
                ["tmux", "has-session", "-t", target], capture_output=True, timeout=10,
            )
            if exists.returncode != 0:
                raise KeyError("セッションが見つかりません")
            buffer_name = f"cdauto-{secrets.token_hex(8)}"
            try:
                loaded = subprocess.run(
                    ["tmux", "load-buffer", "-b", buffer_name, "-"],
                    input=payload, capture_output=True, timeout=10,
                )
                if loaded.returncode != 0:
                    raise OSError("tmux input buffer could not be loaded")
                pasted = subprocess.run(
                    ["tmux", "paste-buffer", "-p", "-d", "-b", buffer_name, "-t", target],
                    capture_output=True, timeout=10,
                )
                if pasted.returncode != 0:
                    raise OSError("tmux input could not be pasted")
                if submit:
                    submitted = subprocess.run(
                        ["tmux", "send-keys", "-t", target, "Enter"],
                        capture_output=True, timeout=10,
                    )
                    if submitted.returncode != 0:
                        raise OSError("tmux input could not be submitted")
            finally:
                subprocess.run(
                    ["tmux", "delete-buffer", "-b", buffer_name],
                    capture_output=True, timeout=10, check=False,
                )
            return len(payload)
        session = self._fallback.get(sid)
        if session is None or not session.alive():
            raise KeyError("セッションが見つかりません")
        connection = TerminalConnection(
            master_fd=session.master_fd, pid=session.pid, owns_process=False, session=session,
        )
        return connection.write(payload + (b"\r" if submit else b""))

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
        rows, cols = _normalize_terminal_size(rows, cols)
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
                ["tmux", "capture-pane", "-p", "-e", "-J", "-S", f"-{HISTORY_REPLAY_LINES}", "-t", target],
                capture_output=True, timeout=15,
            )
            if captured.returncode != 0:
                os.close(master)
                os.killpg(os.getpgid(proc.pid), signal.SIGHUP)
                raise KeyError("セッションが見つかりません")
            # capture-pane中にtmux attachが追加する最終全画面描画も、
            # history_resetより前の初期化frameへ取り込む。これをreaderへ
            # 流すとhistory_end後に1行ずれた画面が露出する。ローカル
            # PTYの最初の追加byteだけ最大20ms待ち、受信後は5msのquiet
            # boundaryで即座に完了する。
            initial += _initial_pty_output(
                master, first_timeout=0.02, quiet_timeout=0.005,
            )
            replay, _ = _bounded_history(captured.stdout.replace(b"\n", b"\r\n"))
            return TerminalConnection(master_fd=master, pid=proc.pid, owns_process=True,
                                      replay=replay, initial=initial, rows=rows, cols=cols,
                                      tmux_target=target)
        s = self._fallback.get(sid)
        if s is None or not s.alive():
            raise KeyError("セッションが見つかりません")
        _set_winsize(s.master_fd, rows, cols)
        s.attached = True
        return TerminalConnection(
            master_fd=s.master_fd, pid=s.pid, owns_process=False,
            replay=(HISTORY_TRUNCATED if s.buffer_truncated else b"") + bytes(s.buffer), session=s,
            rows=rows, cols=cols,
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
        rows: int = 24,
        cols: int = 80,
        tmux_target: str | None = None,
    ) -> None:
        self.master_fd = master_fd
        self.pid = pid
        self.owns_process = owns_process  # True: 切断時に attach プロセスを終了（tmux 側は継続）
        self.replay = replay
        self.initial = initial
        self.session = session
        self._last_size = _normalize_terminal_size(rows, cols)
        self.tmux_target = tmux_target

    def write(self, data: bytes) -> int:
        """PTYへ全byteを書き切る。呼出元はevent loop外で実行すること。"""
        view = memoryview(data)
        total = 0
        while total < len(view):
            try:
                written = os.write(self.master_fd, view[total:])
            except InterruptedError:
                continue
            except BlockingIOError:
                # PTY masterはnon-blocking。固定sleepではなく書込み可能通知を待つ。
                _, writable, _ = select.select([], [self.master_fd], [], 1.0)
                if not writable:
                    raise TimeoutError("PTY write timed out")
                continue
            if written <= 0:
                raise OSError("PTY write returned no progress")
            total += written
        return total

    def scroll_history(self, lines: int) -> bool:
        """Move tmux copy-mode history without accepting a command from the client."""
        if not self.tmux_target:
            return False
        amount = min(100, max(1, abs(lines)))
        direction = "scroll-down" if lines > 0 else "scroll-up"
        entered = subprocess.run(
            ["tmux", "copy-mode", "-e", "-t", self.tmux_target],
            capture_output=True, timeout=5, check=False,
        )
        if entered.returncode != 0:
            raise OSError("tmux history mode is unavailable")
        moved = subprocess.run(
            ["tmux", "send-keys", "-t", self.tmux_target, "-X", "-N", str(amount), direction],
            capture_output=True, timeout=5, check=False,
        )
        if moved.returncode != 0:
            raise OSError("tmux history could not be moved")
        if direction == "scroll-down":
            position = subprocess.run(
                ["tmux", "display-message", "-p", "-t", self.tmux_target, "#{scroll_position}"],
                capture_output=True, text=True, timeout=5, check=False,
            )
            if position.returncode == 0 and position.stdout.strip() in {"", "0"}:
                self.exit_history()
        return True

    def exit_history(self) -> bool:
        """Leave copy mode before terminal input resumes; harmless when not active."""
        if not self.tmux_target:
            return False
        result = subprocess.run(
            ["tmux", "send-keys", "-t", self.tmux_target, "-X", "cancel"],
            capture_output=True, timeout=5, check=False,
        )
        # tmux returns non-zero when the pane is already in normal mode.
        return result.returncode == 0

    def capture_replay(self) -> bytes:
        """resume journal範囲外時だけ現在のbounded snapshotを取得する。"""
        if self.tmux_target:
            captured = subprocess.run(
                ["tmux", "capture-pane", "-p", "-e", "-J", "-S", f"-{HISTORY_REPLAY_LINES}", "-t", self.tmux_target],
                capture_output=True, timeout=15,
            )
            if captured.returncode != 0:
                raise OSError("tmux history capture failed")
            replay, _ = _bounded_history(captured.stdout.replace(b"\n", b"\r\n"))
            return replay
        if self.session is None:
            return self.replay
        prefix = HISTORY_TRUNCATED if self.session.buffer_truncated else b""
        return prefix + bytes(self.session.buffer)

    def resize(self, rows: int, cols: int) -> tuple[int, int]:
        size = _normalize_terminal_size(rows, cols)
        if size == self._last_size:
            return size
        _set_winsize(self.master_fd, *size)
        # start_new_sessionで起動したtmux attachは、このPTY構成ではioctlだけで
        # SIGWINCHを受けない場合がある。ControlDeck所有process groupへ明示通知する。
        if self.owns_process:
            try:
                os.killpg(os.getpgid(self.pid), signal.SIGWINCH)
            except OSError:
                pass
        self._last_size = size
        return size

    def size_diagnostics(self) -> dict[str, object]:
        """明示debug resize時だけ取得する。通常resizeのhot pathではsubprocessを実行しない。"""
        rows, cols = _get_winsize(self.master_fd)
        result: dict[str, object] = {"ptyRows": rows, "ptyCols": cols}
        if not self.tmux_target:
            return result
        commands = {
            "tmuxWindow": [
                "tmux", "display-message", "-p", "-t", self.tmux_target,
                "#{window_width}x#{window_height}",
            ],
            "tmuxClients": [
                "tmux", "list-clients", "-t", self.tmux_target, "-F",
                "#{client_width}x#{client_height}",
            ],
            "tmuxWindowSizePolicy": [
                "tmux", "show-options", "-gvw", "-t", self.tmux_target, "window-size",
            ],
        }
        for key, argv in commands.items():
            completed = subprocess.run(argv, capture_output=True, text=True, timeout=5)
            result[key] = completed.stdout.strip() if completed.returncode == 0 else "N/A"
        return result

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
                    self.session.activity_at = time.time()
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
