import asyncio
import time
from pathlib import Path

from tests.conftest import CSRF_HEADERS


def test_asr_status_reports_reusable_runtime(admin_client, monkeypatch, tmp_path):
    import app.workflows.asr as asr

    monkeypatch.setattr(asr, "runtime_root", lambda: tmp_path)
    monkeypatch.setattr(asr, "is_ready", lambda: True)
    monkeypatch.setattr(asr, "haas_ready", lambda: True)   # 状態は選んだエンジンが使えるかで決まる
    response = admin_client.get("/api/v1/chat/asr/status")
    assert response.status_code == 200
    assert response.json()["state"] == "ready"
    assert response.json()["reusable"] is True


def test_asr_install_job_is_reused_while_running(admin_client, monkeypatch):
    import app.workflows.asr as asr

    async def slow_install(job):
        await asyncio.sleep(0.4)
        return {"ready": True}

    monkeypatch.setattr(asr, "install", slow_install)
    monkeypatch.setattr(asr, "is_ready", lambda: False)
    asr._install_job_id = None
    first = admin_client.post("/api/v1/chat/asr/install-jobs", headers=CSRF_HEADERS)
    second = admin_client.post("/api/v1/chat/asr/install-jobs", headers=CSRF_HEADERS)
    assert first.status_code == 201
    assert second.status_code == 201
    assert second.json() == {"job_id": first.json()["job_id"], "reused": True}
    time.sleep(0.5)


def test_asr_transcribe_converts_and_cleans_temp(admin_client, monkeypatch, tmp_path):
    import app.workflows.asr as asr

    runtime = tmp_path / "runtime"
    data = tmp_path / "data"
    runtime.mkdir()
    binary = runtime / "whisper-cli"
    model = runtime / "ggml-large-v3-turbo.bin"
    binary.write_text("binary")
    model.write_text("model")
    monkeypatch.setattr(asr, "is_ready", lambda: True)
    monkeypatch.setattr(asr, "whisper_ready", lambda: True)
    monkeypatch.setattr(asr, "haas_ready", lambda: False)
    monkeypatch.setattr(asr, "filter_ready", lambda: False)
    monkeypatch.setattr(asr, "binary_path", lambda: binary)
    monkeypatch.setattr(asr, "model_path", lambda: model)
    monkeypatch.setattr(asr, "data_dir", lambda: data)
    monkeypatch.setattr(asr.shutil, "which", lambda name: f"/usr/bin/{name}")

    async def fake_exec(*args: str, cwd: Path, timeout: float):
        if "ffmpeg" in args[0]:
            Path(args[-1]).write_bytes(b"wav")
        else:
            output = Path(args[args.index("-of") + 1])
            output.with_suffix(".txt").write_text("こんにちは Control Deck", encoding="utf-8")
        return 0, ""

    monkeypatch.setattr(asr, "_exec_capture", fake_exec)
    response = admin_client.post(
        "/api/v1/chat/asr/transcribe", headers=CSRF_HEADERS,
        files={"audio": ("voice.webm", b"fake-audio", "audio/webm")},
    )
    assert response.status_code == 200
    assert response.json()["text"] == "こんにちは Control Deck"
    assert response.json()["model"] == "large-v3-turbo"
    assert list((data / "tmp" / "asr").iterdir()) == []


def test_asr_transcribe_rejects_non_audio(admin_client, monkeypatch):
    import app.workflows.asr as asr

    monkeypatch.setattr(asr, "is_ready", lambda: True)
    response = admin_client.post(
        "/api/v1/chat/asr/transcribe", headers=CSRF_HEADERS,
        files={"audio": ("note.txt", b"not audio", "text/plain")},
    )
    assert response.status_code == 422



def test_haas_is_used_when_installed_and_whisper_is_the_fallback(admin_client, monkeypatch, tmp_path):
    """既定は Haas-ja。失敗しても whisper があれば音声入力を止めない（2026-11-18 利用者の依頼で採用）。"""
    from app.workflows import asr

    monkeypatch.setattr(asr, "data_dir", lambda: tmp_path)
    monkeypatch.setattr(asr, "haas_ready", lambda: True)
    monkeypatch.setattr(asr, "whisper_ready", lambda: True)
    monkeypatch.setattr(asr.shutil, "which", lambda name: f"/usr/bin/{name}")

    async def fake_exec(*args, cwd, timeout):
        if "ffmpeg" in args[0]:
            Path(args[args.index("-c:a") + 2]).write_bytes(b"RIFF")
            return 0, ""
        Path(args[args.index("-of") + 1] + ".txt").write_text("控えの結果", encoding="utf-8")
        return 0, ""

    async def haas(engine, wav, cwd, meta=None):
        return "こんにちは。"

    monkeypatch.setattr(asr, "_exec_capture", fake_exec)
    monkeypatch.setattr(asr, "_transcribe_sherpa", haas)
    files = {"audio": ("a.webm", b"x", "audio/webm")}
    first = admin_client.post("/api/v1/chat/asr/transcribe", files=files, headers=CSRF_HEADERS).json()
    assert first == {"text": "こんにちは。", "language": "ja", "model": "Haas-ja-v1.0"}

    async def broken(engine, wav, cwd, meta=None):
        raise RuntimeError("boom")

    monkeypatch.setattr(asr, "_transcribe_sherpa", broken)
    second = admin_client.post("/api/v1/chat/asr/transcribe", files=files, headers=CSRF_HEADERS).json()
    assert second["text"] == "控えの結果" and asr.last_fallback.startswith("RuntimeError") and asr.fallback_worked
    asr.last_fallback, asr.fallback_worked = "", False



def test_repeated_phrases_are_collapsed_and_short_segments_keep_enough_context():
    """短い区切りで whisper が同じ言葉を繰り返した（2026-11-18 本番の画面・手元の音声で再現）。"""
    from app.workflows import asr

    assert asr.clean_transcript("電流波形のFFTも追加してほしいです。電流波形のFFTも追加してほしいです。") == "電流波形のFFTも追加してほしいです。"
    assert asr.clean_transcript("日本には約7000日本には約7000日本には約7000") == "日本には約7000"
    assert asr.clean_transcript("はいはい、わかりました") == "はいはい、わかりました"
    args = asr.speed_args(44 + 32000 * 3)
    assert int(args[args.index("-ac") + 1]) >= 768


def test_selected_asr_engine_is_tried_first_and_missing_choice_falls_back(admin_client, monkeypatch, tmp_path):
    """Models の TTS / ASR で選んだ ASR を先に使う。選んだものが未導入なら入っている方で動かす
    （2026-11-19 利用者「利用するエンジンを選択できるように」）。"""
    from app.speech import settings
    from app.workflows import asr

    monkeypatch.setattr(settings, "data_dir", lambda: tmp_path)
    monkeypatch.setattr(asr, "data_dir", lambda: tmp_path)
    monkeypatch.setattr(asr, "haas_ready", lambda: True)
    monkeypatch.setattr(asr, "whisper_ready", lambda: True)
    monkeypatch.setattr(asr.shutil, "which", lambda name: f"/usr/bin/{name}")

    async def fake_exec(*args, cwd, timeout):
        if "ffmpeg" in args[0]:
            Path(args[args.index("-c:a") + 2]).write_bytes(b"RIFF")
            return 0, ""
        Path(args[args.index("-of") + 1] + ".txt").write_text("whisper の結果", encoding="utf-8")
        return 0, ""

    async def haas(engine, wav, cwd, meta=None):
        return "Haas の結果"

    monkeypatch.setattr(asr, "_exec_capture", fake_exec)
    monkeypatch.setattr(asr, "_transcribe_sherpa", haas)
    files = {"audio": ("a.webm", b"x", "audio/webm")}

    selected = admin_client.put("/api/v1/speech/engines", json={"asr": "whisper"}, headers=CSRF_HEADERS)
    assert selected.status_code == 200 and selected.json()["selected"]["asr"] == "whisper"
    assert asr.engine_order() == ["whisper", "haas"]
    assert admin_client.post("/api/v1/chat/asr/transcribe", files=files, headers=CSRF_HEADERS).json()["model"] == "large-v3-turbo"

    monkeypatch.setattr(asr, "whisper_ready", lambda: False)
    assert asr.engine_order() == ["haas"]
    assert admin_client.post("/api/v1/chat/asr/transcribe", files=files, headers=CSRF_HEADERS).json()["model"] == "Haas-ja-v1.0"

    assert admin_client.put("/api/v1/speech/engines", json={"asr": "unknown"}, headers=CSRF_HEADERS).status_code == 422
    (tmp_path / "speech-engines.json").write_text("{broken", encoding="utf-8")
    assert settings.load() == settings.DEFAULTS


def test_english_choice_never_falls_back_to_a_japanese_engine(admin_client, monkeypatch, tmp_path):
    """英語（Parakeet）を選んだら、日本語専用の Haas へは回さない（英語を片仮名の意味の無い文にして送らない）。
    whisper を英語にしていれば控えにする（2026-11-19 利用者「英会話をしたい」）。"""
    from app.speech import settings
    from app.workflows import asr

    monkeypatch.setattr(settings, "data_dir", lambda: tmp_path)
    monkeypatch.setattr(asr, "data_dir", lambda: tmp_path)
    monkeypatch.setattr(asr, "haas_ready", lambda: True)
    monkeypatch.setattr(asr, "parakeet_ready", lambda: False)
    monkeypatch.setattr(asr, "whisper_ready", lambda: False)
    settings.save({"asr": "parakeet-en"})
    assert asr.engine_order() == []
    monkeypatch.setattr(asr.shutil, "which", lambda name: f"/usr/bin/{name}")

    async def fake_exec(*args, cwd, timeout):
        Path(args[-1]).write_bytes(b"RIFF")
        return 0, ""

    monkeypatch.setattr(asr, "_exec_capture", fake_exec)
    files = {"audio": ("a.webm", b"x", "audio/webm")}
    assert admin_client.post("/api/v1/chat/asr/transcribe", files=files, headers=CSRF_HEADERS).status_code == 409

    monkeypatch.setattr(asr, "whisper_ready", lambda: True)
    assert asr.engine_order() == ["whisper"]
    monkeypatch.setattr(asr, "parakeet_ready", lambda: True)
    assert asr.engine_order() == ["parakeet-en", "whisper"]
    settings.save({"asr": "whisper", "whisper_language": "en"})
    assert asr.engine_order() == ["whisper", "parakeet-en"]
    settings.save({"whisper_language": "auto"})
    assert asr.engine_order() == ["whisper"]


def test_filter_rejects_segments_without_speech_and_does_not_fall_back(admin_client, monkeypatch, tmp_path):
    """入力フィルター（Silero VAD）で声が無いとされた区切りは文字にせず、控えのエンジンにも回さない。
    根拠: 2026-11-19 実測で Haas-ja は雑音だけの 96 本中 37 本に「うん。」「音楽」を出した（会話モードでそのまま送られる）。"""
    import json as jsonlib

    from app.speech import settings
    from app.workflows import asr

    monkeypatch.setattr(settings, "data_dir", lambda: tmp_path)
    monkeypatch.setattr(asr, "data_dir", lambda: tmp_path)
    monkeypatch.setattr(asr, "filter_ready", lambda: True)
    sent = []

    class FakeProcess:
        returncode = 0

        async def communicate(self, data):
            sent.append(jsonlib.loads(data))
            return jsonlib.dumps({"text": "", "rejected": "no_speech", "speech_sec": 0.0, "level_db": -31.0}).encode(), b""

    async def fake_spawn(*args, **kwargs):
        return FakeProcess()

    monkeypatch.setattr(asr.asyncio, "create_subprocess_exec", fake_spawn)
    before = asr.gate_counts["no_speech"]
    try:
        asyncio.run(asr._run_sherpa("haas", tmp_path / "a.wav", tmp_path))
        raise AssertionError("捨てるはず")
    except asr.HTTPException as exc:
        assert exc.status_code == 422
    assert asr.gate_counts["no_speech"] == before + 1
    # 推奨の設定が worker に渡る（VAD 0.5・250 ms、雑音除去なし、音量の門なし）
    assert sent[0]["vad"]["threshold"] == 0.5 and sent[0]["vad"]["min_speech_ms"] == 250
    assert sent[0]["denoise"] is None and sent[0]["min_level_db"] == -90.0
    # フィルターを切ると門も雑音除去も渡さない
    settings.save({"input_filter": {"enabled": False}})
    assert asr._sherpa_request("haas", tmp_path / "a.wav")["vad"] is None


def test_auto_ja_en_routes_each_segment_by_detected_language(admin_client, monkeypatch, tmp_path):
    """日英自動: 区切りごとに言語を判定し、日本語は Haas、英語は Parakeet に渡す。応答の言語・モデルも判定に従う。
    控えは日英を聞ける whisper（自動判定）だけ（2026-11-19 利用者「日本語と英語が交互に来るときは」）。"""
    from app.speech import settings
    from app.workflows import asr

    monkeypatch.setattr(settings, "data_dir", lambda: tmp_path)
    monkeypatch.setattr(asr, "data_dir", lambda: tmp_path)
    for name in ("haas_ready", "parakeet_ready", "lid_ready"):
        monkeypatch.setattr(asr, name, lambda: True)
    monkeypatch.setattr(asr, "whisper_ready", lambda: True)
    settings.save({"asr": "auto-ja-en"})
    assert asr.engine_order() == ["auto-ja-en"]                 # whisper が日本語固定なら控えにしない
    settings.save({"whisper_language": "auto"})
    assert asr.engine_order() == ["auto-ja-en", "whisper"]
    request = asr._sherpa_request("auto-ja-en", tmp_path / "a.wav")
    assert request["model"] is None and set(request["route"]) == {"lid", "ja", "en"}
    assert request["route"]["ja"]["feature_dim"] == 128 and "parakeet" in request["route"]["en"]["encoder"]

    monkeypatch.setattr(asr.shutil, "which", lambda name: f"/usr/bin/{name}")

    async def fake_exec(*args, cwd, timeout):
        Path(args[-1]).write_bytes(b"RIFF")
        return 0, ""

    spoken = iter([("Can you help me?", "en"), ("宿題を手伝って。", "ja")])

    async def routed(engine, wav, cwd, meta=None):
        text, language = next(spoken)
        meta["language"] = language
        return text

    monkeypatch.setattr(asr, "_exec_capture", fake_exec)
    monkeypatch.setattr(asr, "_transcribe_sherpa", routed)
    files = {"audio": ("a.webm", b"x", "audio/webm")}
    first = admin_client.post("/api/v1/chat/asr/transcribe", files=files, headers=CSRF_HEADERS).json()
    second = admin_client.post("/api/v1/chat/asr/transcribe", files=files, headers=CSRF_HEADERS).json()
    assert first == {"text": "Can you help me?", "language": "en", "model": "Parakeet-TDT-0.6B-v2"}
    assert second == {"text": "宿題を手伝って。", "language": "ja", "model": "Haas-ja-v1.0"}
