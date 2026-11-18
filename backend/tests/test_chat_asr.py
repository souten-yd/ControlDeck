import asyncio
import time
from pathlib import Path

from tests.conftest import CSRF_HEADERS


def test_asr_status_reports_reusable_runtime(admin_client, monkeypatch, tmp_path):
    import app.workflows.asr as asr

    monkeypatch.setattr(asr, "runtime_root", lambda: tmp_path)
    monkeypatch.setattr(asr, "is_ready", lambda: True)
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

    async def haas(wav, cwd):
        return "こんにちは。"

    monkeypatch.setattr(asr, "_exec_capture", fake_exec)
    monkeypatch.setattr(asr, "_transcribe_haas", haas)
    files = {"audio": ("a.webm", b"x", "audio/webm")}
    first = admin_client.post("/api/v1/chat/asr/transcribe", files=files, headers=CSRF_HEADERS).json()
    assert first == {"text": "こんにちは。", "language": "ja", "model": "Haas-ja-v1.0"}

    async def broken(wav, cwd):
        raise RuntimeError("boom")

    monkeypatch.setattr(asr, "_transcribe_haas", broken)
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
