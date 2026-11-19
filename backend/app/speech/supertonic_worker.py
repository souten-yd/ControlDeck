"""Supertonic 3 で 1 つの文を WAV にする。専用 venv の python -I で動かす（本体へ読み込まない）。

標準入力に JSON {text, voice, lang, speed, steps, model_dir, threads, output}。重みは保管庫から読み、取りに行かない。"""
import json
import sys
import wave

import numpy as np
from supertonic import TTS


def main() -> None:
    request = json.loads(sys.stdin.buffer.read().decode("utf-8"))
    tts = TTS(model="supertonic-3", model_dir=request["model_dir"], auto_download=False,
              intra_op_num_threads=int(request["threads"]))
    style = tts.get_voice_style(request["voice"])
    waveform, _ = tts.synthesize(request["text"], voice_style=style, lang=request["lang"],
                                 speed=float(request["speed"]), total_steps=int(request["steps"]))
    samples = (np.clip(waveform[0], -1.0, 1.0) * 32767).astype("<i2")
    with wave.open(request["output"], "wb") as target:
        target.setnchannels(1)
        target.setsampwidth(2)
        target.setframerate(int(tts.sample_rate))
        target.writeframes(samples.tobytes())


if __name__ == "__main__":
    main()
