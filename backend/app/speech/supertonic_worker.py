"""Supertonic 3 で 1 つの文を WAV にする。専用 venv の python -I で動かす（本体へ読み込まない）。

標準入力に JSON {text, voice, lang, speed, steps, model_dir, threads, output}。重みは保管庫から読み、取りに行かない。
モデルが扱えない文字（絵文字の後ろの異体字セレクタ U+FE0F など）があると SDK は文ごと失敗する。そのときは
その文字だけを除いて 1 回やり直す（2026-11-19 本番: 「いいお天気ですね☀️」の読み上げが失敗した）。"""
import json
import sys
import wave
from pathlib import Path

import numpy as np
from supertonic import TTS


def supported_only(text: str, indexer_path: Path) -> str:
    """モデルの文字表（unicode_indexer: 符号位置 → 番号、-1 は非対応）に無い文字を除く。"""
    indexer = json.loads(indexer_path.read_text(encoding="utf-8"))
    kept = "".join(ch for ch in text if ch.isspace() or (ord(ch) < len(indexer) and indexer[ord(ch)] != -1))
    if not kept.strip():
        raise ValueError("読める文字がありません")
    return kept


def main() -> None:
    request = json.loads(sys.stdin.buffer.read().decode("utf-8"))
    tts = TTS(model="supertonic-3", model_dir=request["model_dir"], auto_download=False,
              intra_op_num_threads=int(request["threads"]))
    style = tts.get_voice_style(request["voice"])

    def run(text: str):
        return tts.synthesize(text, voice_style=style, lang=request["lang"],
                              speed=float(request["speed"]), total_steps=int(request["steps"]))[0]

    try:
        waveform = run(request["text"])
    except ValueError as error:
        if "unsupported character" not in str(error):
            raise
        waveform = run(supported_only(request["text"], Path(request["model_dir"]) / "onnx" / "unicode_indexer.json"))
    samples = (np.clip(waveform[0], -1.0, 1.0) * 32767).astype("<i2")
    with wave.open(request["output"], "wb") as target:
        target.setnchannels(1)
        target.setsampwidth(2)
        target.setframerate(int(tts.sample_rate))
        target.writeframes(samples.tobytes())


if __name__ == "__main__":
    main()
