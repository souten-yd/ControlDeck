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


# 同じ内容の記録は 1 分に 20 件まで（警告の連発でファイルが膨らまないように）。超えた分は数だけ数える
_RATE_WINDOW = 60.0
_RATE_LIMIT = 20
_rate: dict[str, list] = {}


def record(kind: str, *, rate_key: str = "", **fields) -> dict:
    """1 件残す。書けなくても例外を出さない（診断のせいで本体を止めない）。"""
    now = time.time()
    event = {"ts": now, "kind": kind, **fields}
    with _lock:
        if rate_key:
            window = _rate.get(rate_key)
            if window is None or now - window[0] > _RATE_WINDOW:
                if window and window[2]:
                    event["suppressed_before"] = window[2]
                _rate[rate_key] = window = [now, 0, 0]
                if len(_rate) > 5000:
                    _rate.clear()
            window[1] += 1
            if window[1] > _RATE_LIMIT:
                window[2] += 1
                return event
        _recent.append(event)
        try:
            with _file(datetime.now()).open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(event, ensure_ascii=False, default=str) + "\n")
        except OSError:
            pass
    return event


def read(since: float = 0.0, kinds: set[str] | None = None, limit: int | None = 2000) -> list[dict]:
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
            if limit is not None and len(out) >= limit:
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
