"""診断の記録の置き場（data/diagnostics/events-YYYYMMDD.jsonl、7 日分）。

書くのは警告以上・詰まり・遅い要求など「おかしかったこと」だけで、量は小さい。
1 行 1 件の JSON。ロックはプロセス内だけ（uvicorn は 1 プロセス）。
"""
from __future__ import annotations

import json
import threading
import time
from collections import deque
from datetime import datetime, timedelta
from pathlib import Path

from app.config import data_dir

KEEP_DAYS = 7
_lock = threading.Lock()
_recent: deque[dict] = deque(maxlen=2000)


def root() -> Path:
    path = data_dir() / "diagnostics"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _file(day: datetime) -> Path:
    return root() / f"events-{day:%Y%m%d}.jsonl"


def record(kind: str, **fields) -> dict:
    """1 件残す。書けなくても例外を出さない（診断のせいで本体を止めない）。"""
    event = {"ts": time.time(), "kind": kind, **fields}
    with _lock:
        _recent.append(event)
        try:
            with _file(datetime.now()).open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(event, ensure_ascii=False, default=str) + "\n")
        except OSError:
            pass
    return event


def read(since: float = 0.0, kinds: set[str] | None = None, limit: int = 2000) -> list[dict]:
    """新しい順。ファイルから読む（再起動の前の記録も含む）。"""
    out: list[dict] = []
    days = sorted(root().glob("events-*.jsonl"), reverse=True)
    for path in days:
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError:
            continue
        for line in reversed(lines):
            try:
                event = json.loads(line)
            except ValueError:
                continue
            if event.get("ts", 0) < since:
                return out
            if kinds and event.get("kind") not in kinds:
                continue
            out.append(event)
            if len(out) >= limit:
                return out
    return out


def prune() -> None:
    cutoff = datetime.now() - timedelta(days=KEEP_DAYS)
    for path in root().glob("events-*.jsonl"):
        try:
            if datetime.strptime(path.stem.split("-", 1)[1], "%Y%m%d") < cutoff:
                path.unlink()
        except (ValueError, OSError):
            continue
