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
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

from app.config import data_dir, get_config

TMUX_PREFIX = "cdterm-"
TERMINAL_ENGINES = {"v1", "v2-lab"}
SESSION_ID_RE = re.compile(r"^[0-9a-f]{8}$")
# AI Commander（app/commander）がコンソール用のセッションへ付ける印。
# 一覧で読み返すため、option の名前はここで固定する（値は commander 側で組み立てる）。
COMMANDER_MARK_KEYS = ("cli", "account", "label", "role", "created_by",
                       # C3: 自動再開（off で止める・次の予定・送った回数・最後に送った時刻）
                       "autoresume", "resume_at", "resume_count", "resume_last",
                       # コンソールごとの再開の文言（空なら全体の設定）
                       "resume_message",
                       # コンソールの鍵（ロック）。外から閉じられないようにする（2026-11-28）。
                       # 値は "1" だけ。manager.kill_session がこの印を見て断る（中央の保護）。
                       "locked",
                       # 開いたときのモデル・考えるレベル（鍵をかけたときの保存・開き直しに使う）
                       "model", "effort")
# ロック中のセッションを閉じようとしたときの文言（409 の detail にもそのまま使う）。
SESSION_LOCKED_MESSAGE = "ロック中です。先にロックを外してください"
# create_session(env=) で受け付ける環境変数の名前と値の上限。
ENV_KEY_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
ENV_VALUE_MAX = 4096
# list-sessions の形式文字列へ足す AI Commander の印の部分。
COMMANDER_MARK_FORMAT = "".join(f"\t#{{@cd-commander-{key}}}" for key in COMMANDER_MARK_KEYS)
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


class SessionStateError(RuntimeError):
    """tmux の状態を確認・変更できない。終了や復旧を進めてはいけない。"""


def _tmux_missing(result: subprocess.CompletedProcess, target: str) -> bool:
    """tmux の失敗が「対象のセッションが無い」と確かに言えるときだけ True。

    ローカル tmux 3.4 を専用 -L（試験用の TMUX_TMPDIR）で実測した文言（2026-11-28・LC_ALL=C）:
    - サーバーはあるが対象が無い: has-session / kill-session は "can't find session: <target>"
    - サーバーが無い: "no server running on <socket>"（古いソケットのファイルが残る場合も同じ）
    - ソケットのファイルが無い: "error connecting to <socket> (No such file or directory)"
    - 権限が無い: "error connecting to <socket> (Permission denied)" → 欠落ではない（確認不能）
    いずれも終了値 1。権限・通信・不明な失敗を欠落と誤認すると、閉じた扱い・二重の復旧になる。
    """
    error = (result.stderr or "").strip()
    return result.returncode == 1 and (
        error == f"can't find session: {target}"
        or error.startswith("no server running on /")
        or (error.startswith("error connecting to /") and error.endswith(" (No such file or directory)"))
    )


class SessionLockedError(RuntimeError):
    """ロック（@cd-commander-locked=1）のかかったセッションを閉じようとした。

    manager.kill_session がこの例外を出す（中央の保護・強制解除の抜け道は無い）。
    endpoint は 409 へ直す。利用者が画面（terminal.use）で外すまで閉じられない。
    """


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


def _apply_commander_marks(session: dict, values: list[str]) -> None:
    """AI Commander の印（@cd-commander-*）を一覧の行へ読み込む。

    印の無いセッション（既存の Terminal と同じ）は鍵を足さない。
    values は COMMANDER_MARK_KEYS の順で、tmux の出力そのまま（空文字列=印なし）。
    """
    if not values or not values[0]:
        return
    for key, value in zip(COMMANDER_MARK_KEYS, values):
        if value:
            session[f"commander_{key}"] = value


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
    # AI Commander の印（tmux user option と同じ内容。fallback でも一覧へ出す）
    marks: dict[str, str] = field(default_factory=dict)

    def alive(self) -> bool:
        try:
            os.kill(self.pid, 0)
            return True
        except OSError:
            return False


# ロックの切替（set_locked）と閉じる（kill_session）を競合させないための排他。
# この process の中だけの話。外のプロセスが tmux kill-session を直接打つことは
# 防げないので、それは検知（commander/locks.py）で受け止める。
_guard = threading.RLock()


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
                 "\t#{@control-deck-engine}" + COMMANDER_MARK_FORMAT],
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
                    session = {
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
                    }
                    _apply_commander_marks(session, parts[9:])
                    sessions.append(session)
            return sessions
        # フォールバック
        dead = [sid for sid, s in self._fallback.items() if not s.alive()]
        for sid in dead:
            self._close_fallback(sid)
        sessions = []
        for s in self._fallback.values():
            entry = {
                "id": s.id,
                "name": s.name,
                "created_at": s.created_at,
                "attached": s.attached,
                "persistent": False,
                "engine": s.engine,
                **self._fallback_metadata(s),
            }
            _apply_commander_marks(entry, [s.marks.get(key, "") for key in COMMANDER_MARK_KEYS])
            sessions.append(entry)
        return sessions

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
        env: dict[str, str] | None = None, session_id: str | None = None,
    ) -> dict:
        """セッションを作成する。command 指定時はシェルでそのコマンドを実行する
        （例: gh auth login。継続利用するコマンド側で exec bash 等を付ける）。

        env 指定時はその環境変数をセッションへ渡す（tmux の new-session -e、
        フォールバック時は子プロセスの環境）。値は呼び出し側で検証済みのものだけを
        受ける（この module では名前と文字種の上限だけ確かめる）。"""
        cfg = get_config().terminal
        if engine not in TERMINAL_ENGINES:
            raise ValueError("unsupported terminal engine")
        if not cfg.enabled:
            raise RuntimeError("ターミナルは無効化されています")
        if len(self.list_sessions()) >= cfg.max_sessions:
            raise RuntimeError(f"セッション数の上限（{cfg.max_sessions}）に達しています")
        if env:
            for key, value in env.items():
                if not ENV_KEY_RE.fullmatch(key) or not isinstance(value, str):
                    raise ValueError("セッションの環境変数の名前が不正です")
                if not value or len(value) > ENV_VALUE_MAX or any(c in value for c in "\x00\n\r"):
                    raise ValueError(f"セッションの環境変数 {key} の値が不正です")
        sid = session_id or secrets.token_hex(4)
        _target(sid)
        if session_id is not None and self.session_exists(sid):
            raise SessionStateError("復旧先のセッションが既にあります")
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
            base = ["tmux", "-f", config_path, "new-session", "-d", "-s", name, "-c", workdir]
            if env:
                # -e KEY=VALUE は 1 つの引数として渡す（シェルの文字列へ連結しない）
                for key, value in env.items():
                    base += ["-e", f"{key}={value}"]
            base.append(command or cfg.shell)
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
            **(env or {}),
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
        """セッションを閉じる。ロック（@cd-commander-locked=1）のかかったセッションは断る。

        保護はここ（全ての閉じる経路が通る唯一の場所）で持つ。endpoint 側の前置き確認は
        便宜的なもので、ここを通らない閉じ方は無い（強制解除の引数も無い）。ロックの
        切替（set_locked）と同じ排他の中で確認するので、切替と閉じるが競合しない
        （2026-11-28 利用者「ロックをかけたものは落とさないように」）。
        """
        from app.commander import locks

        with _guard:
            # 印の読取失敗・保存破損でも保護する。途中の復旧先もこの記録で守る。
            if locks.find_entry(sid) is not None or self.is_locked(sid):
                raise SessionLockedError(SESSION_LOCKED_MESSAGE)
            if tmux_available():
                target = _target(sid)
                result = self._run_tmux(["kill-session", "-t", target])
                # 既に無い（終わった殻・外から消えた）ときだけ「閉じた」と同じ扱い。
                # 権限・通信・不明な失敗は成功にしない（実終了失敗の偽装をしない）。
                if result.returncode != 0 and not _tmux_missing(result, target):
                    raise SessionStateError("セッションを閉じられませんでした")
            else:
                self._close_fallback(sid, kill=True)

    @staticmethod
    def _run_tmux(args: list[str]) -> subprocess.CompletedProcess:
        """エラー分類用の文言はローカル tmux の C locale に固定する。timeout・起動失敗は確認不能。"""
        try:
            return subprocess.run(
                ["tmux", *args], capture_output=True, text=True, timeout=5,
                env={**os.environ, "LC_ALL": "C"},
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise SessionStateError("tmux の状態を確認・変更できませんでした") from exc

    @classmethod
    def _checked_tmux(cls, args: list[str]) -> subprocess.CompletedProcess:
        result = cls._run_tmux(args)
        if result.returncode != 0:
            raise SessionStateError("tmux の操作に失敗しました")
        return result

    def set_locked(self, sid: str, locked: bool) -> None:
        """保存・見回り・終了と共通の排他で印を変える。失敗は必ず呼出し側へ返す。"""
        with _guard:
            if locked and tmux_available():
                # timeout・起動失敗も SessionStateError へそろえる（成功か不明かを呼出し側が確かめる）
                self._checked_tmux(["set-option", "-t", _target(sid), "@cd-commander-locked", "1"])
            elif locked:
                self.tag_session(sid, {"locked": "1"})
            elif self.session_exists(sid):
                self.clear_marks(sid, ("locked",))

    def is_locked(self, sid: str) -> bool:
        """unset と確認不能を区別する。-q は対象不明も隠すため使わない。

        ローカル tmux(1) と専用 -L の実測（2026-11-28）:
        unset は invalid option、対象不明は no such session。ほかは保護を継続する。
        """
        target = _target(sid)
        if tmux_available():
            result = self._run_tmux(["show-options", "-v", "-t", target, "@cd-commander-locked"])
            if result.returncode == 0:
                value = result.stdout.strip()
                if value not in ("", "1"):
                    raise SessionStateError("ロックの印が不正です")
                return value == "1"
            if result.returncode == 1 and result.stderr.strip() == "invalid option: @cd-commander-locked":
                return False
            if not self.session_exists(sid):
                return False
            raise SessionStateError("ロックの状態を確認できませんでした")
        session = self._fallback.get(sid)
        return session is not None and session.marks.get("locked") == "1"

    def session_exists(self, sid: str) -> bool:
        """has-session の終了値 1 だけでは欠落と判定しない（権限エラーも 1）。

        ローカル tmux(1) と専用 -L で確認した欠落の文言だけを受け入れる。
        通信・権限・不明なエラーでは例外を出し、復旧による複製を防ぐ。
        """
        target = _target(sid)
        if tmux_available():
            result = self._run_tmux(["has-session", "-t", target])
            if result.returncode == 0:
                return True
            if _tmux_missing(result, target):
                return False
            raise SessionStateError("セッションの存在を確認できませんでした")
        return sid in self._fallback

    def clear_marks(self, sid: str, keys: tuple[str, ...]) -> None:
        """印の解除失敗を成功扱いにしない。"""
        unknown = set(keys) - set(COMMANDER_MARK_KEYS)
        if unknown:
            raise ValueError("不明なセッション属性です")
        with _guard:
            if tmux_available():
                for key in keys:
                    self._checked_tmux(["set-option", "-u", "-t", _target(sid), f"@cd-commander-{key}"])
                return
            session = self._fallback.get(sid)
            if session is not None:
                session.marks = {k: v for k, v in session.marks.items() if k not in keys}

    def capture_text(self, sid: str, lines: int = 60) -> str:
        """画面の最後の lines 行を、色の印を外した文字で返す（AI Commander の状態の判定用）。

        tmux の capture-pane（-J で折り返しをつなぐ）。読めないときは空文字列。
        """
        lines = max(1, min(int(lines), 500))
        if tmux_available():
            try:
                result = subprocess.run(
                    ["tmux", "capture-pane", "-p", "-J", "-S", f"-{lines}", "-t", _target(sid)],
                    capture_output=True, text=True, errors="replace", timeout=5,
                )
            except (OSError, subprocess.TimeoutExpired, KeyError):
                return ""
            return result.stdout if result.returncode == 0 else ""
        session = self._fallback.get(sid)
        if session is None:
            return ""
        text = bytes(session.buffer[-64_000:]).decode("utf-8", errors="replace")
        return "\n".join(re.sub(r"\x1b\[[0-9;?]*[ -/]*[@-~]", "", text).splitlines()[-lines:])

    def session_cwd(self, sid: str) -> str:
        """セッションの作業フォルダの実体のパス（一覧の cwd は ~ へ縮めてあるので別に取る）。読めなければ空文字列。"""
        if tmux_available():
            try:
                result = subprocess.run(
                    ["tmux", "display-message", "-p", "-t", _target(sid), "#{pane_current_path}"],
                    capture_output=True, text=True, errors="replace", timeout=5,
                )
            except (OSError, subprocess.TimeoutExpired, KeyError):
                return ""
            return result.stdout.strip() if result.returncode == 0 else ""
        session = self._fallback.get(sid)
        if session is None:
            return ""
        try:
            return os.readlink(Path("/proc") / str(session.pid) / "cwd")
        except OSError:
            return ""

    def tag_session(self, sid: str, marks: dict[str, str]) -> None:
        """セッションへ印（tmux の user option @cd-commander-<key>）を付ける。

        値は tmux へ配列引数で渡す。既定の Terminal の呼び出しでは使わない。
        一覧（list_sessions）で同じ名前で読み返す。
        """
        if not marks:
            return
        unknown = set(marks) - set(COMMANDER_MARK_KEYS)
        if unknown:
            raise ValueError("不明なセッション属性です")
        for key, value in marks.items():
            # 一覧は tab 区切りで読み返すので、tab・改行などの制御文字は入れない。
            limit = 500 if key == "resume_message" else 96
            if (not isinstance(value, str) or not value or len(value) > limit
                    or any(ord(c) < 0x20 or ord(c) == 0x7f for c in value)):
                raise ValueError(f"セッション属性 {key} の値が不正です")
        if tmux_available():
            target = _target(sid)
            for key, value in marks.items():
                tagged = subprocess.run(
                    ["tmux", "set-option", "-t", target, f"@cd-commander-{key}", value],
                    capture_output=True, timeout=10,
                )
                if tagged.returncode != 0:
                    raise RuntimeError("セッション属性を保存できません")
            return
        session = self._fallback.get(sid)
        if session is None:
            raise KeyError("セッションが見つかりません")
        session.marks = {**session.marks, **marks}

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
            if os.environ.get("TMUX_TMPDIR"):
                # attach の子プロセスも同じソケット置き場を見る（試験の隔離・TMUX_TMPDIR は
                # 本番では設定しないので、設定が無いときは今までどおり）。
                env["TMUX_TMPDIR"] = os.environ["TMUX_TMPDIR"]
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
        entered = subprocess.run(
            ["tmux", "copy-mode", "-e", "-t", self.tmux_target],
            capture_output=True, timeout=5, check=False,
        )
        if entered.returncode != 0:
            raise OSError("tmux history mode is unavailable")
        state = self.history_state()
        distance = max(-HISTORY_LINES, min(HISTORY_LINES, lines))
        position = max(0, min(state["total"], state["position"] - distance))
        # goto-line addresses the copy-mode offset from the live edge directly.
        # Repeating scroll-up N times emits N redraws and overwhelms the browser
        # when the scrollbar jumps across thousands of retained rows.
        moved = subprocess.run(
            ["tmux", "send-keys", "-t", self.tmux_target, "-X", "goto-line", str(position)],
            capture_output=True, timeout=5, check=False,
        )
        if moved.returncode != 0:
            raise OSError("tmux history could not be moved")
        if position == 0:
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

    def history_state(self) -> dict[str, int]:
        """Read the authoritative scroll position, not the client's redraw history."""
        if not self.tmux_target:
            return {"position": 0, "total": 0}
        result = subprocess.run(
            ["tmux", "display-message", "-p", "-t", self.tmux_target,
             "#{scroll_position}|#{history_size}"],
            capture_output=True, text=True, timeout=5, check=False,
        )
        if result.returncode != 0:
            raise OSError("tmux history position is unavailable")
        try:
            position, total = result.stdout.strip().split("|")
            return {"position": max(0, int(position or "0")), "total": max(0, int(total or "0"))}
        except ValueError as exc:
            raise OSError("tmux history position is invalid") from exc

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
