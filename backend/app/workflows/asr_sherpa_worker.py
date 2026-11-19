"""1 つの WAV（16 kHz・16 bit・1 ch）を入力フィルターに通し、sherpa-onnx で文字にする。

専用の venv の python -I で動かす（本体へ読み込まない）。標準入力に JSON:
  {"audio": path, "threads": n,
   "model": {"encoder", "decoder", "joiner", "tokens", "feature_dim"?} | null,   # null は門だけ（whisper の前に使う）
   "vad": {"model", "threshold", "min_speech_ms"} | null,
   "denoise": {"model"} | null, "min_level_db": float}
標準出力の最後の行に JSON: {"text", "rejected": null|"too_quiet"|"no_speech", "speech_sec", "level_db"}

門（2026-11-19 実測、docs/speech-input-filter.md）: Haas-ja は雑音だけの区切りにも「うん。」「音楽」などを出す。
Silero VAD v5 は雑音 96 本をすべて声なし、話した声 1062 区切りをすべて声ありとした。声の無い区切りは文字にしない。"""
import json
import sys
import wave

import numpy as np
import sherpa_onnx


def level_db(x: np.ndarray) -> float:
    """区切りの声の大きさ: 20 ms ごとの RMS の上位 10%（黙っている部分に引っ張られない）。"""
    frames = x[: len(x) // 320 * 320].reshape(-1, 320)
    if len(frames) == 0:
        return -120.0
    rms = np.sqrt((frames ** 2).mean(axis=1) + 1e-12)
    return float(20 * np.log10(np.percentile(rms, 90) + 1e-9))


def speech_seconds(x: np.ndarray, vad: dict) -> float:
    config = sherpa_onnx.VadModelConfig()
    config.sample_rate = 16000
    config.num_threads = 1
    config.silero_vad.model = vad["model"]
    config.silero_vad.threshold = float(vad["threshold"])
    config.silero_vad.min_speech_duration = 0.1
    config.silero_vad.min_silence_duration = 0.2
    detector = sherpa_onnx.VoiceActivityDetector(config, buffer_size_in_seconds=max(30, len(x) / 16000 + 5))
    for start in range(0, len(x), 512):
        detector.accept_waveform(x[start:start + 512])
    detector.flush()
    total = 0
    while not detector.empty():
        total += len(detector.front.samples)
        detector.pop()
    return total / 16000


def denoise(x: np.ndarray, model: str, threads: int) -> np.ndarray:
    config = sherpa_onnx.OfflineSpeechDenoiserConfig()
    config.model.num_threads = threads
    config.model.gtcrn.model = model
    out = sherpa_onnx.OfflineSpeechDenoiser(config).run(x, 16000)
    return np.asarray(out.samples, dtype=np.float32)


def main() -> None:
    request = json.loads(sys.stdin.buffer.read().decode("utf-8"))
    with wave.open(request["audio"]) as source:
        x = np.frombuffer(source.readframes(source.getnframes()), dtype=np.int16).astype(np.float32) / 32768
    result = {"text": "", "rejected": None, "speech_sec": None, "level_db": round(level_db(x), 1)}
    if result["level_db"] < float(request.get("min_level_db", -90.0)):
        result["rejected"] = "too_quiet"
    elif request.get("vad"):
        result["speech_sec"] = round(speech_seconds(x, request["vad"]), 2)
        if result["speech_sec"] * 1000 < float(request["vad"]["min_speech_ms"]):
            result["rejected"] = "no_speech"
    model = request.get("model")
    if result["rejected"] is None and model:
        if request.get("denoise"):
            x = denoise(x, request["denoise"]["model"], int(request["threads"]))
        extra = {"feature_dim": int(model["feature_dim"])} if model.get("feature_dim") else {}
        recognizer = sherpa_onnx.OfflineRecognizer.from_transducer(
            encoder=model["encoder"], decoder=model["decoder"], joiner=model["joiner"], tokens=model["tokens"],
            model_type="nemo_transducer", num_threads=int(request["threads"]), **extra)
        stream = recognizer.create_stream()
        stream.accept_waveform(16000, x)
        recognizer.decode_stream(stream)
        result["text"] = stream.result.text
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
