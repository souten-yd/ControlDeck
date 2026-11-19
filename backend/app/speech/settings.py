"""TTS / ASR の設定。data/speech-engines.json に置く。

- 使うエンジンの選択（2026-11-19 利用者「利用するエンジンを選択できるように」）
- エンジンごとの設定（同日「選択した TTS / ASR に対応する設定項目を出す」）
- 音声入力のフィルター（同日「音が小さい・ノイズが大きい場合を除去するフィルター。有効無効と詳細を変えられ、
  初めは推奨のプリセット」）。中身と既定値の根拠は FILTER_PRESETS の注記と docs/speech-input-filter.md。

決めた値以外は受け取らない。未知の値や壊れたファイルは既定へ戻す（音声入力を止めない）。"""
from __future__ import annotations

import copy
import json
import logging
import os
from pathlib import Path

from app.config import data_dir

logger = logging.getLogger("control_deck.speech")

ASR_ENGINES = ("haas", "parakeet-en", "auto-ja-en", "whisper")
TTS_ENGINES = ("supertonic-3",)
TTS_VOICES = ("F1", "F2", "F3", "F4", "F5", "M1", "M2", "M3", "M4", "M5")
SPEED_RANGE = (0.7, 2.0)
WHISPER_LANGUAGES = ("ja", "en", "auto")
TTS_LANGUAGES = ("auto", "ja", "en")
DENOISERS = ("off", "gtcrn")

# 入力フィルターのプリセット。根拠（2026-11-19 実測、docs/speech-input-filter.md）:
# - 雑音だけの 3 秒（ファン・うなり・キーボード・ピンク雑音、96 本）で Haas-ja は 37 本に「うん。」「音楽」などを出した。
#   Silero VAD v5（閾値 0.5）は 96 本すべてを声なし（0 秒）とし、話した声 1062 区切り（日 24 文・英 73 文 × 雑音・小音量）は
#   すべて声あり（区切りの 43% 以上）だった → VAD で「声の無い区切り」を捨てるのが推奨の中心。
# - 音量だけの門は使えない: 小さな声（-48 dB）と雑音（-48 dB）が同じ帯にある。既定は使わない（-90）。
# - 雑音除去は英語（Parakeet・実音声）で悪化（人の声の雑音 10 dB: 5.5% → GTCRN 11.5%）、日本語（Haas・合成音声）で
#   わずかに改善（平均 1.9% → 1.2%）。どのプリセットでも切り、日本語で試す任意の設定として残す。
# - 音量の均し（speechnorm）は日英とも悪化したので設定に置かない。低い音のカット（80 Hz）は差が無く、うなりでわずかに良い。
FILTER_PRESETS: dict[str, dict[str, object]] = {
    "recommended": {"vad": True, "vad_threshold": 0.5, "min_speech_ms": 250, "min_level_db": -90.0,
                    "denoise": "off", "highpass": False,
                    "mic_sensitivity": 0.02, "turn_end_ms": 1200, "browser_processing": True},
    # 騒がしい場所: 声と判定する基準を上げ、短い音を捨て、低いうなりを切る。マイクの感度も下げる
    "noisy": {"vad": True, "vad_threshold": 0.6, "min_speech_ms": 400, "min_level_db": -90.0,
              "denoise": "off", "highpass": True,
              "mic_sensitivity": 0.04, "turn_end_ms": 1200, "browser_processing": True},
    # 静かな部屋・小さな声: 声の基準とマイクの感度を下げる
    "quiet": {"vad": True, "vad_threshold": 0.4, "min_speech_ms": 200, "min_level_db": -90.0,
              "denoise": "off", "highpass": False,
              "mic_sensitivity": 0.01, "turn_end_ms": 1200, "browser_processing": True},
}
FILTER_PRESET_NAMES = (*FILTER_PRESETS, "custom")
FILTER_RANGES: dict[str, tuple[float, float]] = {
    "vad_threshold": (0.1, 0.95), "min_speech_ms": (0, 3000), "min_level_db": (-90.0, -10.0),
    "mic_sensitivity": (0.002, 0.2), "turn_end_ms": (500, 5000),
}

DEFAULTS: dict[str, object] = {
    "asr": "haas", "tts": "supertonic-3",
    # TTS（Supertonic 3）
    "tts_voice": "F1", "tts_speed": 1.05, "tts_steps": 8, "tts_language": "auto",
    # ASR（whisper だけ言語を選べる。Haas は日本語、Parakeet は英語に固定）
    "whisper_language": "ja",
    "input_filter": {"enabled": True, "preset": "recommended", **FILTER_PRESETS["recommended"]},
}


def _number(value: object, low: float, high: float) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and low <= value <= high


def _valid_filter(value: object) -> bool:
    if not isinstance(value, dict) or set(value) != set(DEFAULTS["input_filter"]):   # type: ignore[arg-type]
        return False
    for key, item in value.items():
        if key in ("enabled", "vad", "highpass", "browser_processing"):
            if not isinstance(item, bool):
                return False
        elif key == "preset":
            if item not in FILTER_PRESET_NAMES:
                return False
        elif key == "denoise":
            if item not in DENOISERS:
                return False
        elif not _number(item, *FILTER_RANGES[key]):
            return False
    return True


def _valid(kind: str, value: object) -> bool:
    checks = {
        "asr": lambda v: v in ASR_ENGINES,
        "tts": lambda v: v in TTS_ENGINES,
        "tts_voice": lambda v: v in TTS_VOICES,
        "tts_speed": lambda v: _number(v, *SPEED_RANGE),
        "tts_steps": lambda v: isinstance(v, int) and not isinstance(v, bool) and 2 <= v <= 32,
        "tts_language": lambda v: v in TTS_LANGUAGES,
        "whisper_language": lambda v: v in WHISPER_LANGUAGES,
        "input_filter": _valid_filter,
    }
    return kind in checks and bool(checks[kind](value))


def _path() -> Path:
    return (data_dir() / "speech-engines.json").resolve()


def load() -> dict[str, object]:
    try:
        raw = json.loads(_path().read_text(encoding="utf-8"))
    except FileNotFoundError:
        raw = {}
    except (OSError, json.JSONDecodeError):
        logger.warning("speech-engines.json を読めないため既定の設定を使います")
        raw = {}
    if not isinstance(raw, dict):
        raw = {}
    values: dict[str, object] = {}
    for kind, default in DEFAULTS.items():
        value = raw.get(kind)
        if kind == "input_filter" and isinstance(value, dict):
            # 足した項目は既定で補う（古い版の設定ファイルでもフィルターを外さない）
            value = {**copy.deepcopy(default), **value}   # type: ignore[dict-item]
        values[kind] = value if _valid(kind, value) else copy.deepcopy(default)
    return values


def selected(kind: str) -> str:
    return str(load()[kind])


def input_filter() -> dict[str, object]:
    return dict(load()["input_filter"])   # type: ignore[arg-type]


def save(values: dict[str, object]) -> dict[str, object]:
    current = load()
    for kind, value in values.items():
        if kind == "input_filter" and isinstance(value, dict):
            value = merge_filter(current["input_filter"], value)   # type: ignore[arg-type]
        if kind not in DEFAULTS or not _valid(kind, value):
            raise ValueError(f"選べない値です: {kind}={value}")
        current[kind] = value
    path = _path()
    temp = path.with_name(path.name + ".tmp")
    temp.write_text(json.dumps(current, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temp, path)
    return current


def merge_filter(current: dict[str, object], change: dict[str, object]) -> dict[str, object]:
    """プリセットを選ぶとその値に揃える。細かい値を変えたら「カスタム」にする。"""
    merged = {**current, **change}
    if "preset" in change and change["preset"] in FILTER_PRESETS:
        merged.update(FILTER_PRESETS[str(change["preset"])])
    elif set(change) - {"enabled", "preset"}:
        merged["preset"] = next((name for name, values in FILTER_PRESETS.items()
                                 if all(merged[k] == v for k, v in values.items())), "custom")
    return merged
