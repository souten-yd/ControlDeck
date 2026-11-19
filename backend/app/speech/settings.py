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
TTS_VOICES = ("F1", "F2", "F3", "F4", "F5", "M1", "M2", "M3", "M4", "M5")
# 読み上げの既定の声と速さ（AI アシスタントの TTS 返答・会話モードが使う。Models の TTS で変える）
DEFAULTS: dict[str, object] = {"asr": "haas", "tts": "supertonic-3", "tts_voice": "F1", "tts_speed": 1.05}
CHOICES = {"asr": ASR_ENGINES, "tts": TTS_ENGINES, "tts_voice": TTS_VOICES}
SPEED_RANGE = (0.7, 2.0)


def _valid(kind: str, value: object) -> bool:
    if kind == "tts_speed":
        return isinstance(value, (int, float)) and not isinstance(value, bool) and SPEED_RANGE[0] <= value <= SPEED_RANGE[1]
    return kind in CHOICES and value in CHOICES[kind]


def _path() -> Path:
    return (data_dir() / "speech-engines.json").resolve()


def load() -> dict[str, object]:
    try:
        raw = json.loads(_path().read_text(encoding="utf-8"))
    except FileNotFoundError:
        raw = {}
    except (OSError, json.JSONDecodeError):
        logger.warning("speech-engines.json を読めないため既定のエンジンを使います")
        raw = {}
    if not isinstance(raw, dict):
        raw = {}
    return {kind: (raw.get(kind) if _valid(kind, raw.get(kind)) else DEFAULTS[kind]) for kind in DEFAULTS}


def selected(kind: str) -> str:
    return str(load()[kind])


def save(values: dict[str, object]) -> dict[str, object]:
    current = load()
    for kind, value in values.items():
        if kind not in DEFAULTS or not _valid(kind, value):
            raise ValueError(f"選べない値です: {kind}={value}")
        current[kind] = value
    path = _path()
    temp = path.with_name(path.name + ".tmp")
    temp.write_text(json.dumps(current, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temp, path)
    return current
