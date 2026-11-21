"""見張りモード（ライブ確認）のファイルの置き場。ControlDeck 側（runs・webview）が使う。

- spec（見張り役への指示。鍵付きの {base} を含むのでプロジェクトの外、data の下、0600）
- 状態 `<project>/.controldeck/live/<run_id>.jsonl`（見張り役が 1 行ずつ書く）
- AI からの知らせ `<project>/.controldeck/live/<run_id>.notes`（AI が 1 行書く）
- 画面の記録 `<project>/.controldeck/feedback/<run_id>.jsonl`（ControlDeck が追記する）
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from app.config import data_dir


def spec_path(run_id: int) -> Path:
    return data_dir() / "project-lab" / "live" / f"{int(run_id)}.json"


def write_spec(run_id: int, spec: dict[str, Any]) -> Path:
    """spec を 0600 で書く。同じ run の再書き込みは置き換える。"""
    path = spec_path(run_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    os.chmod(path.parent, 0o700)
    temp = path.with_suffix(".tmp")
    fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        json.dump(spec, handle, ensure_ascii=False)
    os.replace(temp, path)
    return path


def read_spec(run_id: int) -> dict[str, Any] | None:
    try:
        raw = json.loads(spec_path(run_id).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return raw if isinstance(raw, dict) else None


def remove_spec(run_id: int) -> None:
    try:
        spec_path(run_id).unlink()
    except OSError:
        pass


def state_file(project: Path, run_id: int) -> Path:
    return project / ".controldeck" / "live" / f"{int(run_id)}.jsonl"


def notes_file(project: Path, run_id: int) -> Path:
    return project / ".controldeck" / "live" / f"{int(run_id)}.notes"


def feedback_file(project: Path, run_id: int) -> Path:
    return project / ".controldeck" / "feedback" / f"{int(run_id)}.jsonl"
