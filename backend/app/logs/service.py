"""アプリログ（stdout/stderr ファイル）の読み取り。"""
from __future__ import annotations

import re
import subprocess
from collections.abc import Iterator
from pathlib import Path

from app.config import data_dir

STREAMS = ("stdout", "stderr")
MAX_STREAM_LINE_BYTES = 1024 * 1024
_AUTH_LINE = re.compile(r'''(?im)(["']?\b(?:authorization|cookie)\b["']?[ \t]*[:=][ \t]*)([^\r\n]*)''')
_SECRET_ASSIGNMENT = re.compile(
    r'''(?ix)
    (["']?\b(?:token|secret|password|passwd|pass|api[_-]?token|api[_-]?key|private[_-]?key|auth|cookie)\b["']?
    [ \t]*[:=][ \t]*)
    (?:"(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*'|[^\s,;&]+)
    ''',
)
_SYSTEMD_UNIT = re.compile(r"^[A-Za-z0-9@_.-]+\.service$")
_JOURNALCTL_CANDIDATES = (Path("/usr/bin/journalctl"), Path("/bin/journalctl"))


def redact_text(value: str, sensitive_values: set[str] | None = None) -> str:
    """ログ表示用に既知の秘密値と秘密名付き代入を不可逆マスクする。"""
    redacted = value
    for secret in sorted(sensitive_values or set(), key=len, reverse=True):
        if len(secret) >= 4:
            redacted = redacted.replace(secret, "***")
    redacted = _AUTH_LINE.sub(r"\1***", redacted)
    return _SECRET_ASSIGNMENT.sub(r"\1***", redacted)


class RedactedLineBuffer:
    """任意byte chunkを改行境界でredactする有界buffer。巨大行の本文は破棄する。"""

    def __init__(self, sensitive_values: set[str] | None = None) -> None:
        self._pending = bytearray()
        self._dropping_oversized = False
        self._sensitive_values = sensitive_values or set()

    def feed(self, data: bytes) -> str:
        output: list[str] = []
        remaining = data
        if self._dropping_oversized:
            newline = remaining.find(b"\n")
            if newline < 0:
                return ""
            remaining = remaining[newline + 1:]
            self._dropping_oversized = False
        self._pending.extend(remaining)
        while True:
            newline = self._pending.find(b"\n")
            if newline < 0:
                if len(self._pending) > MAX_STREAM_LINE_BYTES:
                    self._pending.clear()
                    self._dropping_oversized = True
                    output.append("[1MiBを超える改行なしログ行を省略]\n")
                break
            if newline > MAX_STREAM_LINE_BYTES:
                del self._pending[:newline + 1]
                output.append("[1MiBを超える改行なしログ行を省略]\n")
                continue
            raw = bytes(self._pending[:newline])
            del self._pending[:newline + 1]
            output.append(redact_text(raw.decode("utf-8", errors="replace"), self._sensitive_values) + "\n")
        return "".join(output)

    def finish(self) -> str:
        if self._dropping_oversized or not self._pending:
            self._pending.clear()
            return ""
        raw = bytes(self._pending)
        self._pending.clear()
        return redact_text(raw.decode("utf-8", errors="replace"), self._sensitive_values)


def log_path(app_id: int, stream: str) -> Path:
    if stream not in STREAMS:
        raise ValueError(f"不正なストリーム: {stream}")
    root = (data_dir() / "logs").resolve()
    root.mkdir(parents=True, exist_ok=True)
    app_dir = (root / str(app_id)).resolve()
    try:
        app_dir.relative_to(root)
    except ValueError as error:
        raise ValueError("ログパスが許可ルート外です") from error
    app_dir.mkdir(parents=True, exist_ok=True)
    path = (app_dir / f"{stream}.log").resolve()
    try:
        path.relative_to(app_dir)
    except ValueError as error:
        raise ValueError("ログパスがアプリログ領域外です") from error
    return path


def tail_lines(
    path: Path,
    max_lines: int,
    max_bytes: int = 2 * 1024 * 1024,
    sensitive_values: set[str] | None = None,
) -> list[str]:
    """ファイル末尾から最大 max_lines 行を読む（末尾 max_bytes のみ走査）。"""
    if not path.exists():
        return []
    size = path.stat().st_size
    with path.open("rb") as f:
        if size > max_bytes:
            f.seek(size - max_bytes)
            f.readline()  # 途中行を捨てる
        data = f.read()
    lines = data.decode("utf-8", errors="replace").splitlines()
    return [
        "[1MiBを超える改行なしログ行を省略]"
        if len(line.encode("utf-8", errors="replace")) > MAX_STREAM_LINE_BYTES
        else redact_text(line, sensitive_values)
        for line in lines[-max_lines:]
    ]


def read_new_bytes(path: Path, offset: int, max_bytes: int = 256 * 1024) -> tuple[bytes, int]:
    """offset以降の追記byteを有界に読み、truncate時は先頭へ戻る。"""
    if not path.exists():
        return b"", 0
    size = path.stat().st_size
    if size < offset:
        offset = 0
    if size == offset:
        return b"", offset
    with path.open("rb") as handle:
        handle.seek(offset)
        data = handle.read(max_bytes)
    return data, offset + len(data)


def read_new_data(path: Path, offset: int, max_bytes: int = 256 * 1024) -> tuple[str, int]:
    """offset 以降の追記分を読み、(テキスト, 新 offset) を返す。ローテーション時は末尾へ追従。"""
    data, new_offset = read_new_bytes(path, offset, max_bytes)
    return data.decode("utf-8", errors="replace"), new_offset


def iter_redacted_file(path: Path, sensitive_values: set[str] | None = None) -> Iterator[bytes]:
    """download用。秘密値を行境界でマスクし、巨大行は内容を返さない。"""
    buffer = RedactedLineBuffer(sensitive_values)
    with path.open("rb") as handle:
        while chunk := handle.read(256 * 1024):
            text = buffer.feed(chunk)
            if text:
                yield text.encode("utf-8")
    final = buffer.finish()
    if final:
        yield final.encode("utf-8")


def journal_lines(
    unit: str,
    scope: str,
    max_lines: int,
    sensitive_values: set[str] | None = None,
) -> list[str]:
    """登録済みunitのjournalを固定argv・有界件数で取得する。"""
    if _SYSTEMD_UNIT.fullmatch(unit) is None or scope not in ("user", "system"):
        raise ValueError("systemdログ対象が不正です")
    executable = next((str(path) for path in _JOURNALCTL_CANDIDATES if path.is_file()), None)
    if executable is None:
        raise OSError("journalctlが利用できません")
    argv = [executable]
    if scope == "user":
        argv.append("--user")
    argv.extend(["--unit", unit, "--output=short-iso", "--no-pager", "--lines", str(min(2000, max(1, max_lines)))])
    result = subprocess.run(argv, capture_output=True, timeout=5, check=False, shell=False)
    if result.returncode not in (0, 1):
        raise OSError("journalを取得できません")
    raw = result.stdout[-2 * 1024 * 1024:].decode("utf-8", errors="replace")
    return [redact_text(line, sensitive_values) for line in raw.splitlines()[-max_lines:]]
