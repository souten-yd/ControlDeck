"""見張りモードの画面との口（ControlDeck 側）。

- 画面（live.js）から来た記録を `<project>/.controldeck/feedback/<run_id>.jsonl` へ追記する
  （1 件 16KB・1 秒 50 件・1 run 20MB まで）。各行に受けた時刻と `source: "page"`
- 見張り役の状態ファイルと、AI の知らせのファイルの「新しい行」を読む
"""
from __future__ import annotations

import collections
import json
import os
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.project_lab import live_paths

MAX_RECORD_BYTES = 16 * 1024
MAX_RECORDS_PER_SECOND = 50
MAX_FEEDBACK_BYTES = 20 * 1024 * 1024
MAX_NOTE_CHARS = 300

_windows: dict[int, collections.deque[float]] = {}
_lock = threading.Lock()


def _rate_ok(run_id: int) -> bool:
    now = time.monotonic()
    with _lock:
        window = _windows.setdefault(run_id, collections.deque())
        while window and now - window[0] >= 1.0:
            window.popleft()
        if len(window) >= MAX_RECORDS_PER_SECOND:
            return False
        window.append(now)
        return True


def forget_run(run_id: int) -> None:
    with _lock:
        _windows.pop(run_id, None)


def accept_record(project: Path, run_id: int, text: str) -> str | None:
    """記録 1 件を追記する。断ったときは理由（too_large / rate / invalid / quota）を返す。"""
    data = text.encode("utf-8", errors="replace")
    if len(data) > MAX_RECORD_BYTES:
        return "too_large"
    if not _rate_ok(run_id):
        return "rate"
    try:
        record = json.loads(text)
    except ValueError:
        return "invalid"
    if not isinstance(record, dict):
        return "invalid"
    line = json.dumps(
        {**{k: v for k, v in record.items() if k not in {"ts", "source"}},
         "ts": datetime.now(timezone.utc).isoformat(), "source": "page"},
        ensure_ascii=False,
    ) + "\n"
    path = live_paths.feedback_file(project, run_id)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        size = path.stat().st_size if path.exists() else 0
        if size + len(line.encode("utf-8")) > MAX_FEEDBACK_BYTES:
            return "quota"
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        with os.fdopen(fd, "a", encoding="utf-8") as handle:
            handle.write(line)
    except OSError:
        return "io"
    return None


def read_new_lines(path: Path, offset: int) -> tuple[list[str], int]:
    """offset 以降の、改行まで書き終わった行を返す。書きかけの最後の行は次回へ回す。"""
    try:
        size = path.stat().st_size
    except OSError:
        return [], 0 if offset else offset
    if size < offset:
        offset = 0
    if size == offset:
        return [], offset
    with path.open("rb") as handle:
        handle.seek(offset)
        raw = handle.read(size - offset)
    end = raw.rfind(b"\n")
    if end < 0:
        return [], offset
    lines = raw[:end].decode("utf-8", errors="replace").split("\n")
    return [line for line in lines if line.strip()], offset + end + 1


def parse_state(line: str) -> dict[str, Any] | None:
    try:
        value = json.loads(line)
    except ValueError:
        return None
    return value if isinstance(value, dict) and isinstance(value.get("phase"), str) else None
