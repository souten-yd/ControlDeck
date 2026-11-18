"""Haas-ja で 1 つの WAV（16 kHz・16 bit・1 ch）を文字にする。専用の venv の python -I で動かす（本体へ読み込まない）。"""
import json
import sys
import wave

import numpy as np
import sherpa_onnx


def main() -> None:
    model, audio, threads = sys.argv[1], sys.argv[2], int(sys.argv[3])
    recognizer = sherpa_onnx.OfflineRecognizer.from_transducer(
        encoder=f"{model}/encoder.quantized.onnx", decoder=f"{model}/decoder.onnx", joiner=f"{model}/joiner.onnx",
        tokens=f"{model}/tokens.txt", model_type="nemo_transducer", feature_dim=128, num_threads=threads)
    with wave.open(audio) as source:
        samples = np.frombuffer(source.readframes(source.getnframes()), dtype=np.int16).astype(np.float32) / 32768
    stream = recognizer.create_stream()
    stream.accept_waveform(16000, samples)
    recognizer.decode_stream(stream)
    print(json.dumps({"text": stream.result.text}, ensure_ascii=False))


if __name__ == "__main__":
    main()
