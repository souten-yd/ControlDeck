"""Supertonic 3（TTS）の保管庫・導入・合成と、機能追加の「音声エンジン」の扱いを守る。

根拠: 2026-11-19 利用者「Supertonic 3 を TTS として使いたい。開発中止・サポート終了で更新も消去もありうるので、
リポジトリを丸ごとアーカイブとして ControlDeck に保存し、機能追加から導入できるように。導入時は音声モデルも」。
- 保管庫は一度取ったものを取り直さない（上流が消えた・書き換えられた後に失わない）
- アンインストールで消すのは実行環境だけで、保管庫は残す
- 機能追加では「更新」が無く、導入＝利用可能（有効化・再読み込みを求めない）
実際の取得・合成は外部と重い計算なのでここでは模擬し、実機の確認は PR に記録する。"""
import asyncio
import json

import pytest

from tests.conftest import CSRF_HEADERS


@pytest.fixture()
def speech_data(monkeypatch, tmp_path):
    from app.speech import settings, supertonic

    monkeypatch.setattr(supertonic, "data_dir", lambda: tmp_path)
    monkeypatch.setattr(settings, "data_dir", lambda: tmp_path)
    supertonic.last_error = ""
    supertonic.last_archive_error = ""
    return tmp_path


class _Job:
    def __init__(self):
        self.logs = []

    def set_progress(self, *args):
        pass

    def log(self, line):
        self.logs.append(line)


def _fill_model(supertonic, names=None):
    model = supertonic.model_dir()
    for name in names or supertonic.HF_FILES:
        target = model / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"\0" * supertonic.HF_FILES[name][0])


def test_archive_keeps_what_it_has_and_fetches_only_missing(speech_data, monkeypatch):
    from app.speech import supertonic

    _fill_model(supertonic, [n for n in supertonic.HF_FILES if n != "onnx/tts.json"])
    fetched = []

    async def fake_download(client, url, target, size, sha256, root, on_chunk):
        fetched.append(url)
        assert root in target.parents
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"\0" * size)

    async def fake_run(job, *args, cwd, timeout):
        if args[1] == "clone":
            (supertonic.Path(args[-1]) / "HEAD").parent.mkdir(parents=True)
            (supertonic.Path(args[-1]) / "HEAD").write_text("ref: refs/heads/main\n")
        elif "download" in args:
            for spec in supertonic.RUNTIME_PACKAGES[1:]:
                (speech_data / "archives" / "supertonic-3" / "pypi" / f"{spec.replace('==', '-')}-py3-none-any.whl").write_bytes(b"w")

    monkeypatch.setattr(supertonic, "_download", fake_download)
    monkeypatch.setattr(supertonic, "_run", fake_run)
    monkeypatch.setattr(supertonic.shutil, "which", lambda name: "/usr/bin/git")
    status = asyncio.run(supertonic.ensure_archive(_Job()))

    hf = [url for url in fetched if "huggingface.co" in url]
    assert hf == [f"https://huggingface.co/{supertonic.HF_REPO}/resolve/{supertonic.HF_REVISION}/onnx/tts.json"]
    assert status["complete"] and all(status["git"].values())
    manifest = json.loads((speech_data / "archives" / "supertonic-3" / "MANIFEST.json").read_text())
    assert manifest["huggingface"]["revision"] == supertonic.HF_REVISION
    assert any(item["path"].endswith("onnx/vocoder.onnx") for item in manifest["files"])

    # 2 回目は何も取りに行かない（上流が消えても困らない）
    fetched.clear()
    clones = []

    async def no_network(job, *args, cwd, timeout):
        clones.append(args)

    monkeypatch.setattr(supertonic, "_run", no_network)
    assert asyncio.run(supertonic.ensure_archive(_Job()))["complete"]
    assert fetched == [] and clones == []


def test_uninstall_removes_runtime_but_keeps_archive(speech_data):
    from app.speech import supertonic

    _fill_model(supertonic)
    python = supertonic.python_path()
    python.parent.mkdir(parents=True)
    python.write_text("")
    (supertonic.runtime_root() / "venv-ready").write_text(",".join(supertonic.RUNTIME_PACKAGES))
    assert supertonic.ready()
    supertonic.uninstall()
    assert not supertonic.ready() and supertonic.model_ready()


def test_feature_is_a_speech_engine_without_update_or_reload(admin_client, speech_data, monkeypatch):
    from app.features import registry
    from app.speech import supertonic

    item = registry.status("supertonic-3")
    assert item["kind"] == "speech-engine" and item["category"] == "speech" and not item["installed"]
    with pytest.raises(registry.FeatureError):
        registry.install("supertonic-3")   # ネットワークを使うので非同期ジョブだけ

    monkeypatch.setattr(supertonic, "ready", lambda: True)
    assert registry.status("supertonic-3")["enabled"] is True
    # 1 回の合成の失敗は導入の壊れとして出さない（2026-11-19 本番: 「修復が必要」と出て再導入を促した）
    monkeypatch.setattr(supertonic, "last_error", "ValueError: Found 1 unsupported character(s)")
    item = registry.status("supertonic-3")
    assert item["health"] == "healthy" and item["error"] == ""
    with pytest.raises(registry.FeatureError, match="更新はありません"):
        registry.update("supertonic-3")
    response = admin_client.post("/api/v1/features/supertonic-3/disable", headers=CSRF_HEADERS)
    assert response.status_code == 200 and response.json()["requires_reload"] is False


def test_tts_returns_wav_and_validates_input(admin_client, speech_data, monkeypatch):
    from app.speech import supertonic

    calls = []

    async def fake_synthesize(text, *, voice, lang, speed, steps):
        calls.append((text, voice, lang, speed, steps))
        return b"RIFF....WAVE"

    assert admin_client.post("/api/v1/speech/tts", json={"text": "こんにちは"}, headers=CSRF_HEADERS).status_code == 409
    monkeypatch.setattr(supertonic, "ready", lambda: True)
    monkeypatch.setattr(supertonic, "synthesize", fake_synthesize)
    response = admin_client.post("/api/v1/speech/tts", json={"text": "こんにちは", "voice": "M2"}, headers=CSRF_HEADERS)
    assert response.status_code == 200 and response.headers["content-type"] == "audio/wav"
    assert response.content.startswith(b"RIFF") and calls == [("こんにちは", "M2", "ja", 1.05, 8)]
    # 声・速さを省くと、Models の TTS で選んだ既定を使う（AI アシスタントの読み上げ）
    saved = admin_client.put("/api/v1/speech/engines", json={"tts_voice": "M4", "tts_speed": 1.3}, headers=CSRF_HEADERS)
    assert saved.status_code == 200 and saved.json()["selected"]["tts_voice"] == "M4"
    admin_client.post("/api/v1/speech/tts", json={"text": "やあ"}, headers=CSRF_HEADERS)
    assert calls[-1] == ("やあ", "M4", "ja", 1.3, 8)
    assert admin_client.put("/api/v1/speech/engines", json={"tts_speed": 9}, headers=CSRF_HEADERS).status_code == 422
    bad = admin_client.post("/api/v1/speech/tts", json={"text": "x", "voice": "../etc"}, headers=CSRF_HEADERS)
    assert bad.status_code == 422


def test_engine_list_has_both_asr_and_tts(admin_client, speech_data):
    body = admin_client.get("/api/v1/speech/engines").json()
    assert {engine["id"]: engine["kind"] for engine in body["engines"]} == {
        "haas": "asr", "parakeet-en": "asr", "auto-ja-en": "asr", "whisper": "asr", "supertonic-3": "tts"}
    assert {k: body["selected"][k] for k in ("asr", "tts", "tts_voice", "tts_speed")} == {
        "asr": "haas", "tts": "supertonic-3", "tts_voice": "F1", "tts_speed": 1.05}
    assert body["selected"]["input_filter"]["preset"] == "recommended" and body["selected"]["input_filter"]["vad"] is True


def test_synthesis_rejects_out_of_range_before_starting_a_process(speech_data):
    from app.speech import supertonic

    with pytest.raises(ValueError):
        asyncio.run(supertonic.synthesize("x" * (supertonic.MAX_TEXT_CHARS + 1)))
    with pytest.raises(ValueError):
        asyncio.run(supertonic.synthesize("こんにちは", speed=5.0))


def test_input_filter_presets_and_custom_values(admin_client, speech_data):
    """入力フィルター: 初めは推奨。プリセットを選ぶとその値に揃い、細かい値を変えるとカスタムになる。範囲外は受け取らない。
    古い設定ファイル（フィルターの項目が無い・足りない）でも既定で補う（2026-11-19 利用者「有効無効と詳細を変えられ、
    初めはプリセットで推奨の設定に」）。"""
    from app.speech import settings

    put = lambda body: admin_client.put("/api/v1/speech/engines", json=body, headers=CSRF_HEADERS)
    noisy = put({"input_filter": {"preset": "noisy"}}).json()["selected"]["input_filter"]
    assert noisy["vad_threshold"] == 0.6 and noisy["highpass"] is True and noisy["preset"] == "noisy"
    custom = put({"input_filter": {"min_speech_ms": 600}}).json()["selected"]["input_filter"]
    assert custom["preset"] == "custom" and custom["min_speech_ms"] == 600 and custom["vad_threshold"] == 0.6
    back = put({"input_filter": {"preset": "recommended"}}).json()["selected"]["input_filter"]
    assert back == {"enabled": True, "preset": "recommended", **settings.FILTER_PRESETS["recommended"]}
    assert put({"input_filter": {"vad_threshold": 5}}).status_code == 422
    assert put({"input_filter": {"unknown": 1}}).status_code == 422
    assert put({"whisper_language": "en", "tts_steps": 12, "tts_language": "en"}).status_code == 200
    (speech_data / "speech-engines.json").write_text(json.dumps({"asr": "parakeet-en", "input_filter": {"enabled": False}}))
    loaded = settings.load()
    assert loaded["asr"] == "parakeet-en" and loaded["input_filter"]["enabled"] is False
    assert loaded["input_filter"]["vad_threshold"] == 0.5
