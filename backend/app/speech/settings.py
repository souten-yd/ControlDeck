"""TTS / ASR で使うエンジンの選択。data/speech-engines.json に置く（2026-11-19 利用者「利用するエンジンを選択できるように」）。

選べるのはここに定義したエンジンだけ。未知の値や壊れたファイルは既定へ戻す（音声入力を止めない）。"""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path

from app.config import data_dir

logger = logging.getLogger("control_deck.speech")

ASR_ENGINES = ("haas", "whisper")
TTS_ENGINES = ("supertonic-3",)
DEFAULTS = {"asr": "haas", "tts": "supertonic-3"}
CHOICES = {"asr": ASR_ENGINES, "tts": TTS_ENGINES}


def _path() -> Path:
    return (data_dir() / "speech-engines.json").resolve()


def load() -> dict[str, str]:
    try:
        raw = json.loads(_path().read_text(encoding="utf-8"))
    except FileNotFoundError:
        raw = {}
    except (OSError, json.JSONDecodeError):
        logger.warning("speech-engines.json を読めないため既定のエンジンを使います")
        raw = {}
    if not isinstance(raw, dict):
        raw = {}
    return {kind: (raw.get(kind) if raw.get(kind) in CHOICES[kind] else DEFAULTS[kind]) for kind in DEFAULTS}


def selected(kind: str) -> str:
    return load()[kind]


def save(values: dict[str, str]) -> dict[str, str]:
    current = load()
    for kind, engine in values.items():
        if kind not in CHOICES or engine not in CHOICES[kind]:
            raise ValueError(f"選べないエンジンです: {kind}={engine}")
        current[kind] = engine
    path = _path()
    temp = path.with_name(path.name + ".tmp")
    temp.write_text(json.dumps(current, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temp, path)
    return current
