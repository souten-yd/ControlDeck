"""AIチャット用ローカルASR。

既定は Haas-ja-v1.0（量子化版・sherpa-onnx・CPU）。2026-11-18 利用者「高速な日本語 ASR があれば採用」。手元の日本語 12 文で
whisper large-v3-turbo より約 8 倍速く（RTF 0.04 / 0.33）、明らかな誤りも少なく、句読点も付いた。モデル 0.46 GB・実行時
約 0.86 GiB の RAM で GPU は使わない。区切りごとに別プロセスで動かし、本体にメモリを残さない。whisper.cpp は控え。"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import json
import os
import re
import shutil
import sys
import time
import uuid
from pathlib import Path

import httpx
from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile

from app.audit import service as audit
from app.config import data_dir
from app.database import get_db
from app.jobs import service as jobs
from app.models import User
from app.security.deps import require_permission

router = APIRouter(prefix="/chat/asr", tags=["chat-asr"])
logger = logging.getLogger("control_deck.chat.asr")

WHISPER_VERSION = "v1.9.1"
WHISPER_REPO = "https://github.com/ggml-org/whisper.cpp.git"
MODEL_NAME = "large-v3-turbo"
MODEL_URL = "https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-large-v3-turbo.bin"
MODEL_BYTES = 1_624_555_275
MODEL_SHA256 = "1fc70f774d38eb169993ac391eea357ef47c88757ef72ee5943879b7e8e2bc69"
INSTALL_REVISION = "static-1"
MAX_AUDIO_BYTES = 25 * 1024 * 1024
_install_job_id: str | None = None

HAAS_VERSION = "v1"
HAAS_REPO = "https://huggingface.co/oboroge0/Haas-ja-v1.0-onnx-quantized/resolve/7f5ab0a4b1bf39db78afd6691eeecbc4f9d42e39"
HAAS_PACKAGES = ("sherpa-onnx==2.0.1", "sherpa-onnx-core==2.0.1", "numpy==2.5.5")
HAAS_FILES = {
    "decoder.onnx": (15661602, "2947cb5512699cbba753fe45fe221428aadaa9856597debbb7aabe19cc253622"),
    "encoder.data": (410744064, "30befbf6aeb75383e6551f5e58de53efdb76972ddc7d0f76070c517899873586"),
    "encoder.quantized.onnx": (42261252, "aba291d61d3d594bc6fb8c7f90acbd980e7bc7bf6b7caaf3965ddc25e6f42fac"),
    "joiner.onnx": (12158641, "8a68320e750890ff9e944a794b41ae1c42c6f439ac4c273503458a6b42adc12d"),
    "tokens.txt": (28704, "ec78b13c5d9f156cf0d5fdbd6b5aec5785680f319c92630c11d5d11b14b83a84"),
    "LICENSE": (11358, "cfc7749b96f63bd31c3c42b5c471bf756814053e847c10f3eb003417bc523d30"),
    "NOTICE": (783, "279784164ddd8a16f0b42aff954d34bc10485c0064a96c7a852419d95eca5cad"),
}
HAAS_THREADS = 4
_haas_gate = asyncio.Semaphore(1)     # 1 回 約 0.86〜1.5 GiB。同時に動かさない

# 英語の ASR（2026-11-19 利用者「英会話をしたいので英語の入力を受け付ける ASR を選定して導入評価」）。
# Parakeet TDT 0.6B v2（int8・sherpa-onnx）。LibriSpeech 73 文で WER 3.1%（雑音 10 dB 3.3%、人の声の雑音 5.5%）、
# RTF 0.022、読み込み 0.9 秒、RSS 1.5 GB、句読点つき、CC-BY-4.0。候補 5 つの比較は docs/speech-input-filter.md。
PARAKEET_VERSION = "v2"
PARAKEET_REPO = ("https://huggingface.co/csukuangfj/sherpa-onnx-nemo-parakeet-tdt-0.6b-v2-int8/resolve/"
                 "84b478d541cdbf0c296a7ab5f1e20a6f8f998114")
PARAKEET_FILES = {
    "decoder.int8.onnx": (7257753, "4d097bae3bac5c96018d5293ba39f1b657296102b142affda40378f38b020f41"),
    "encoder.int8.onnx": (652282309, "92a10bd9c23936ea0566c29f963bc57c2a5df4261b6a8921c39abea66557ca8b"),
    "joiner.int8.onnx": (1739080, "77a29f3e5ac7d37074e0af465391b80f314015d5b97f0f90c6c7767f1d56f428"),
    "tokens.txt": (9384, "ec182b70dd42113aff6c5372c75cac58c952443eb22322f57bbd7f53977d497d"),
}
# 入力フィルターの資材（どの ASR でも使う）。声の判定（Silero VAD v5）と、任意の雑音除去（GTCRN）
FILTER_FILES = {
    "silero_vad_v5.onnx": (2313101, "6b99cbfd39246b6706f98ec13c7c50c6b299181f2474fa05cbc8046acc274396",
                           "https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/silero_vad_v5.onnx"),
    "gtcrn_simple.onnx": (535638, "e77603ac0c23dac3227dd2d7135b3a585cbee2679048aecfa886657d3ae1b534",
                          "https://github.com/k2-fsa/sherpa-onnx/releases/download/speech-enhancement-models/gtcrn_simple.onnx"),
}
# 日英の自動（2026-11-19 利用者「日本語と英語が混ざる・交互に来るときは」）。区切りごとに Whisper tiny で言語を判定し、
# 日本語は Haas、英語は Parakeet に渡す。実測（docs/speech-input-filter.md）: 判定は英語 146/146・日本語 198/200、
# 誤りは日本語（放送）6.2%・英語 3.1% と専用のエンジンと同じ。日英を 1 つで聞く Qwen3-ASR（放送の日本語 43%、韓国語の文や
# 無い文を作った）・Whisper turbo（19%、4 倍遅い）・SenseVoice（86%）より良い。1 つの文の中の英単語はカタカナになる。
LID_REPO = "https://huggingface.co/csukuangfj/sherpa-onnx-whisper-tiny/resolve/65176e2deb88badc814a94058666cadccc29b61c"
LID_FILES = {
    "tiny-encoder.int8.onnx": (12937772, "d24fb083ae3b1041fc24e97971d60e280c9342201fbb67b0ab428a8b4a51a434"),
    "tiny-decoder.int8.onnx": (89855401, "d2fece8dd42771f1df975c6c0445770d0c292bf7547c2cae04a6c0cc57540925"),
}
ENGINE_LANGUAGE = {"haas": "ja", "parakeet-en": "en", "auto-ja-en": "ja+en"}
gate_counts = {"passed": 0, "no_speech": 0, "too_quiet": 0}   # 入力フィルターで捨てた区切り（起動してから）
last_fallback = ""                    # Haas が失敗した理由（自己診断）
fallback_worked = False               # そのとき whisper で文字にできたか
last_install_error = ""               # 裏の導入に失敗した理由
_install_retry_at = 0.0               # 裏の導入を次に試してよい時刻（失敗を繰り返さない）
INSTALL_RETRY_SECONDS = 6 * 3600.0


def runtime_root() -> Path:
    root = (data_dir() / "runtimes" / "whisper.cpp" / WHISPER_VERSION).resolve()
    root.mkdir(parents=True, exist_ok=True)
    return root


def binary_path() -> Path:
    return runtime_root() / "bin" / "whisper-cli"


def model_path() -> Path:
    return runtime_root() / "models" / f"ggml-{MODEL_NAME}.bin"


def _binary_ready() -> bool:
    binary = binary_path()
    marker = runtime_root() / "install-revision"
    try:
        return (binary.is_file() and os.access(binary, os.X_OK)
                and marker.read_text(encoding="utf-8").strip() == INSTALL_REVISION)
    except OSError:
        return False


def _inside(path: Path, root: Path) -> Path:
    resolved = path.resolve()
    if resolved != root and root not in resolved.parents:
        raise RuntimeError("ASRデータ領域外のパスは使用できません")
    return resolved


def haas_root() -> Path:
    root = (data_dir() / "runtimes" / "asr-haas" / HAAS_VERSION).resolve()
    root.mkdir(parents=True, exist_ok=True)
    return root


def haas_python() -> Path:
    return haas_root() / "venv" / "bin" / "python"


def haas_ready() -> bool:
    root = haas_root()
    try:
        return (haas_python().is_file() and (root / "venv-ready").read_text().strip() == ",".join(HAAS_PACKAGES)
                and all((root / "model" / name).stat().st_size == size for name, (size, _) in HAAS_FILES.items()))
    except OSError:
        return False


def parakeet_root() -> Path:
    root = (data_dir() / "runtimes" / "asr-parakeet-en" / PARAKEET_VERSION).resolve()
    root.mkdir(parents=True, exist_ok=True)
    return root


def filter_root() -> Path:
    root = (data_dir() / "runtimes" / "asr-filter" / "v1").resolve()
    root.mkdir(parents=True, exist_ok=True)
    return root


def sherpa_ready() -> bool:
    """sherpa-onnx の専用 venv（Haas と Parakeet と入力フィルターで共用）"""
    try:
        return haas_python().is_file() and (haas_root() / "venv-ready").read_text().strip() == ",".join(HAAS_PACKAGES)
    except OSError:
        return False


def parakeet_ready() -> bool:
    root = parakeet_root()
    try:
        return sherpa_ready() and all((root / "model" / name).stat().st_size == size for name, (size, _) in PARAKEET_FILES.items())
    except OSError:
        return False


def filter_ready() -> bool:
    root = filter_root()
    try:
        return sherpa_ready() and all((root / name).stat().st_size == size for name, (size, _, _) in FILTER_FILES.items())
    except OSError:
        return False


def lid_root() -> Path:
    root = (data_dir() / "runtimes" / "asr-lid" / "whisper-tiny").resolve()
    root.mkdir(parents=True, exist_ok=True)
    return root


def lid_ready() -> bool:
    root = lid_root()
    try:
        return sherpa_ready() and all((root / name).stat().st_size == size for name, (size, _) in LID_FILES.items())
    except OSError:
        return False


def auto_ready() -> bool:
    """日英の自動: 言語判定と、日本語・英語の両方のエンジンがそろっている"""
    return lid_ready() and haas_ready() and parakeet_ready()


def whisper_ready() -> bool:
    model = model_path()
    return _binary_ready() and model.is_file() and model.stat().st_size == MODEL_BYTES


def is_ready() -> bool:
    return haas_ready() or whisper_ready() or parakeet_ready() or auto_ready()


def engine_ready(engine: str) -> bool:
    return {"haas": haas_ready, "whisper": whisper_ready, "parakeet-en": parakeet_ready, "auto-ja-en": auto_ready}[engine]()


def selected_engine() -> str:
    """Models の「TTS / ASR」で選んだ ASR（2026-11-19 利用者「利用するエンジンを選択できるように」）。"""
    from app.speech import settings

    return settings.selected("asr")


def whisper_language() -> str:
    from app.speech import settings

    return settings.selected("whisper_language")


def engine_order() -> list[str]:
    """試す順。選んだエンジンが使えれば先頭、同じ言語を聞けるエンジンを失敗したときの控えにする（入っていれば）。

    選んだものが未導入なら、同じ言語を聞ける入っている方で動かす（音声入力を止めない）。英語を選んで日本語の
    エンジンへ回すと意味の無い文字になるので、言語の違うエンジンには回さない。どれも無ければ空。"""
    chosen = selected_engine()
    if chosen == "whisper":
        language = whisper_language()
        candidates = ["whisper"] + (["parakeet-en"] if language == "en" else ["haas"] if language == "ja" else ["auto-ja-en"])
    elif chosen == "auto-ja-en":
        # 日英を聞けるのは whisper の自動判定だけ。日本語・英語だけのエンジンには回さない
        candidates = ["auto-ja-en"] + (["whisper"] if whisper_language() == "auto" else [])
    else:
        candidates = [chosen, "whisper"]
    return [engine for engine in candidates if engine_ready(engine)]


def status() -> dict:
    active = jobs.get(_install_job_id) if _install_job_id else None
    order = engine_order()
    engine = order[0] if order else ""
    state = "ready" if order else (
        "installing" if active and active.status in ("queued", "running") else "missing"
    )
    chosen = selected_engine()
    labels = {"haas": ("sherpa-onnx 2.0.1", "Haas-ja-v1.0"), "parakeet-en": ("sherpa-onnx 2.0.1", "Parakeet-TDT-0.6B-v2"),
              "auto-ja-en": ("sherpa-onnx 2.0.1", "Whisper-tiny LID + Haas-ja / Parakeet"),
              "whisper": (f"whisper.cpp {WHISPER_VERSION}", MODEL_NAME), "": ("", "")}
    return {
        "state": state,
        "ready": state == "ready",
        "installing": state == "installing",
        "job_id": active.id if active and active.status in ("queued", "running") else None,
        "runtime": labels[engine][0],
        "model": labels[engine][1],
        "engine": engine,
        "language": ENGINE_LANGUAGE.get(engine, whisper_language() if engine == "whisper" else ""),
        # 選んだエンジンか入力フィルターがまだ無い: 裏で導入する（待たせない。失敗したらしばらく試さない）
        "upgrade_available": (state == "ready" and time.time() >= _install_retry_at
                              and ((chosen != "whisper" and not engine_ready(chosen)) or not filter_ready())),
        "selected": chosen,
        "install_error": last_install_error,
        "filter_ready": filter_ready(),
        "gate": dict(gate_counts),
        "reusable": state == "ready",
    }


async def _run(job: jobs.Job, *args: str, cwd: Path, timeout: float) -> None:
    process = await asyncio.create_subprocess_exec(
        *args, cwd=str(cwd), stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    try:
        output, _ = await asyncio.wait_for(process.communicate(), timeout=timeout)
    except TimeoutError:
        process.kill()
        await process.wait()
        raise RuntimeError(f"{Path(args[0]).name} が時間内に完了しませんでした")
    text = output.decode("utf-8", errors="replace")
    for line in text.splitlines()[-20:]:
        if line.strip():
            job.log(line.strip()[:500])
    if process.returncode != 0:
        raise RuntimeError(f"{Path(args[0]).name} が終了コード {process.returncode} で失敗しました")


async def _download(url: str, target: Path, size: int, sha256: str, root: Path, on_chunk) -> None:
    part = _inside(target.with_name(target.name + ".part"), root)
    part.unlink(missing_ok=True)
    digest = hashlib.sha256()
    received = 0
    try:
        timeout = httpx.Timeout(30.0, read=60.0)   # 止まった接続で導入（と本番の自動更新）を待たせ続けない
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True, headers={"User-Agent": "ControlDeck"}) as client:
            async with client.stream("GET", url) as response:
                response.raise_for_status()
                with part.open("wb") as stream:
                    def store(data: bytes) -> None:
                        stream.write(data)
                        digest.update(data)

                    async for chunk in response.aiter_bytes(1024 * 1024):
                        await asyncio.to_thread(store, chunk)
                        received += len(chunk)
                        on_chunk(len(chunk))
        if received != size or digest.hexdigest() != sha256:
            raise RuntimeError(f"{target.name} のサイズまたは SHA-256 が一致しません")
        part.replace(target)
    except BaseException:
        part.unlink(missing_ok=True)
        raise


async def ensure_sherpa(job: jobs.Job) -> None:
    """sherpa-onnx の専用 venv（Haas・Parakeet・入力フィルターで共用）。"""
    root = haas_root()
    marker = root / "venv-ready"
    wanted = ",".join(HAAS_PACKAGES)
    if not (haas_python().is_file() and marker.is_file() and marker.read_text().strip() == wanted):
        job.set_progress("音声認識の実行環境を準備中", 0, 2)
        marker.unlink(missing_ok=True)   # 作り直しに失敗したら「準備できた」に見せない
        await _run(job, sys.executable, "-m", "venv", "--clear", str(root / "venv"), cwd=root, timeout=180)
        await _run(job, str(haas_python()), "-m", "pip", "install", "--no-input", "--disable-pip-version-check",
                   *HAAS_PACKAGES, cwd=root, timeout=900)
        marker.write_text(wanted + "\n", encoding="utf-8")


async def _fetch_files(job: jobs.Job, label: str, base: str, files: dict, target_dir: Path, root: Path) -> None:
    target_dir.mkdir(parents=True, exist_ok=True)
    total = sum(item[0] for item in files.values())
    done = 0

    def advance(count: int) -> None:
        nonlocal done
        done += count
        job.set_progress(label, done, total)

    for name, item in files.items():
        size, sha256 = item[0], item[1]
        url = item[2] if len(item) > 2 else f"{base}/{name}"
        target = _inside(target_dir / name, root)
        if target.is_file() and target.stat().st_size == size:
            advance(size)
            continue
        await _download(url, target, size, sha256, root, advance)


async def install_filter(job: jobs.Job) -> None:
    """入力フィルターの資材（Silero VAD v5・GTCRN。合わせて 2.8 MB、版とハッシュを固定）。"""
    await ensure_sherpa(job)
    await _fetch_files(job, "入力フィルターを取得中", "", FILTER_FILES, filter_root(), filter_root())


async def install_parakeet(job: jobs.Job) -> None:
    """英語の ASR（Parakeet TDT 0.6B v2、約 0.66 GB、版とハッシュを固定）。"""
    await ensure_sherpa(job)
    root = parakeet_root()
    await _fetch_files(job, "英語の音声認識モデルを取得中", PARAKEET_REPO, PARAKEET_FILES, root / "model", root)
    await install_filter(job)


async def install_auto(job: jobs.Job) -> None:
    """日英の自動（言語判定 Whisper tiny 0.1 GB＋Haas＋Parakeet）。"""
    if not haas_ready():
        await install_haas(job)
    if not parakeet_ready():
        await install_parakeet(job)
    await _fetch_files(job, "言語判定モデルを取得中", LID_REPO, LID_FILES, lid_root(), lid_root())


async def install_haas(job: jobs.Job) -> None:
    """Haas-ja（版固定のモデル・ハッシュ照合）と専用の venv を導入する。"""
    await ensure_sherpa(job)
    root = haas_root()
    model = root / "model"
    model.mkdir(parents=True, exist_ok=True)
    total = sum(size for size, _ in HAAS_FILES.values())
    done = 0

    def advance(count: int) -> None:
        nonlocal done
        done += count
        job.set_progress("音声認識モデルを取得中", done, total)

    for name, (size, sha256) in HAAS_FILES.items():
        target = _inside(model / name, root)
        if target.is_file() and target.stat().st_size == size:
            advance(size)
            continue
        await _download(f"{HAAS_REPO}/{name}", target, size, sha256, root, advance)
    await install_filter(job)


async def install(job: jobs.Job) -> dict:
    """選んだ音声認識（既定は Haas-ja、英語は Parakeet）と入力フィルターを導入する。
    入れられなければ whisper を控えとして使う（無ければ導入する）。"""
    global last_install_error, _install_retry_at
    chosen = selected_engine()
    if chosen == "auto-ja-en" and not auto_ready():
        try:
            await install_auto(job)
        except Exception as exc:  # noqa: BLE001
            last_install_error = f"{type(exc).__name__}: {exc}"[:300]
            _install_retry_at = time.time() + INSTALL_RETRY_SECONDS
            raise
    elif chosen == "parakeet-en" and not parakeet_ready():
        try:
            await install_parakeet(job)
        except Exception as exc:  # noqa: BLE001
            last_install_error = f"{type(exc).__name__}: {exc}"[:300]
            _install_retry_at = time.time() + INSTALL_RETRY_SECONDS
            raise
    elif chosen not in ("parakeet-en", "auto-ja-en") and not haas_ready():
        try:
            await install_haas(job)
            last_install_error = ""
        except Exception as exc:  # noqa: BLE001
            last_install_error = f"{type(exc).__name__}: {exc}"[:300]
            _install_retry_at = time.time() + INSTALL_RETRY_SECONDS
            job.log(f"Haas-ja を導入できませんでした: {last_install_error}")
            if not whisper_ready():
                await install_whisper(job)
    if sherpa_ready() and not filter_ready():
        try:
            await install_filter(job)
        except Exception as exc:  # noqa: BLE001 - フィルターが無くても認識はできる
            last_install_error = f"{type(exc).__name__}: {exc}"[:300]
            _install_retry_at = time.time() + INSTALL_RETRY_SECONDS
            job.log(f"入力フィルターを導入できませんでした: {last_install_error}")
    job.set_progress("ASRを利用できます", 1, 1)
    if not is_ready():
        raise RuntimeError("ASR導入後の検証に失敗しました")
    return status()


async def install_whisper(job: jobs.Job) -> dict:
    """固定タグのwhisper.cppと固定hashの多言語baseモデルを導入する（控え）。"""
    root = runtime_root()
    binary = binary_path()
    model = model_path()
    if whisper_ready():
        job.log("既存のASRランタイムとモデルを再利用します")
        return status()

    git = shutil.which("git")
    cmake = shutil.which("cmake")
    if not git or not cmake:
        raise RuntimeError("ASR導入にはgitとcmakeが必要です")

    if not _binary_ready():
        source_tmp = _inside(root / "source.part", root)
        if source_tmp.exists():
            shutil.rmtree(source_tmp)
        job.set_progress("whisper.cppを取得中", 0, 3)
        await _run(job, git, "clone", "--depth", "1", "--branch", WHISPER_VERSION,
                   WHISPER_REPO, str(source_tmp), cwd=root, timeout=180)
        build = _inside(source_tmp / "build", root)
        job.set_progress("whisper.cppを構成中", 1, 3)
        await _run(job, cmake, "-S", str(source_tmp), "-B", str(build),
                   "-DCMAKE_BUILD_TYPE=Release", "-DBUILD_SHARED_LIBS=OFF",
                   "-DWHISPER_BUILD_TESTS=OFF",
                   cwd=root, timeout=300)
        job.set_progress("whisper.cppをビルド中", 1, 3)
        await _run(job, cmake, "--build", str(build), "--config", "Release",
                   "--target", "whisper-cli", "-j", str(min(os.cpu_count() or 2, 8)),
                   cwd=root, timeout=1200)
        built = _inside(build / "bin" / "whisper-cli", root)
        if not built.is_file():
            raise RuntimeError("ビルド済みwhisper-cliが見つかりません")
        binary.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(built, binary)
        binary.chmod(0o755)
        (root / "install-revision").write_text(INSTALL_REVISION + "\n", encoding="utf-8")
        shutil.rmtree(source_tmp)

    if not model.is_file() or model.stat().st_size != MODEL_BYTES:
        model.parent.mkdir(parents=True, exist_ok=True)
        received = 0

        def advance(count: int) -> None:
            nonlocal received
            received += count
            job.set_progress("音声認識モデルを取得中", received, MODEL_BYTES)

        job.set_progress("音声認識モデルを取得中", 2, 3)
        await _download(MODEL_URL, model, MODEL_BYTES, MODEL_SHA256, root, advance)

    job.set_progress("ASRを利用できます", 3, 3)
    if not whisper_ready():
        raise RuntimeError("ASR導入後の検証に失敗しました")
    return status()


def _sherpa_request(engine: str | None, wav: Path) -> dict:
    """worker への指示。入力フィルターの設定（Models の TTS / ASR）を載せる。"""
    from app.speech import settings

    flt = settings.input_filter()
    use = bool(flt["enabled"]) and filter_ready()
    haas_base, parakeet_base = haas_root() / "model", parakeet_root() / "model"
    models = {
        "haas": {"encoder": str(haas_base / "encoder.quantized.onnx"), "decoder": str(haas_base / "decoder.onnx"),
                 "joiner": str(haas_base / "joiner.onnx"), "tokens": str(haas_base / "tokens.txt"), "feature_dim": 128},
        "parakeet-en": {"encoder": str(parakeet_base / "encoder.int8.onnx"), "decoder": str(parakeet_base / "decoder.int8.onnx"),
                        "joiner": str(parakeet_base / "joiner.int8.onnx"), "tokens": str(parakeet_base / "tokens.txt")},
    }
    route = None
    if engine == "auto-ja-en":
        route = {"lid": {"encoder": str(lid_root() / "tiny-encoder.int8.onnx"), "decoder": str(lid_root() / "tiny-decoder.int8.onnx")},
                 "ja": models["haas"], "en": models["parakeet-en"]}
    return {
        "audio": str(wav), "threads": HAAS_THREADS, "model": models.get(engine or ""), "route": route,
        "vad": {"model": str(filter_root() / "silero_vad_v5.onnx"), "threshold": flt["vad_threshold"],
                "min_speech_ms": flt["min_speech_ms"]} if use and flt["vad"] else None,
        "denoise": {"model": str(filter_root() / "gtcrn_simple.onnx")} if use and flt["denoise"] == "gtcrn" else None,
        "min_level_db": float(flt["min_level_db"]) if use else -120.0,
    }


async def _run_sherpa(engine: str | None, wav: Path, cwd: Path) -> dict:
    worker = Path(__file__).with_name("asr_sherpa_worker.py")
    request = json.dumps(_sherpa_request(engine, wav)).encode("utf-8")
    async with _haas_gate:
        process = await asyncio.create_subprocess_exec(
            str(haas_python()), "-I", str(worker), cwd=str(cwd),
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        try:
            stdout, stderr = await asyncio.wait_for(process.communicate(request), timeout=90)
        except TimeoutError:
            raise RuntimeError("音声認識が時間内に完了しませんでした")
        finally:
            if process.returncode is None:   # 時間切れ・取り消しでも残さない（同時に 1 つを守る）
                process.kill()
                await process.wait()
    if process.returncode != 0:
        raise RuntimeError(stderr.decode("utf-8", errors="replace")[-300:] or "音声認識が失敗しました")
    result = json.loads(stdout.decode("utf-8").strip().splitlines()[-1])
    rejected = result.get("rejected")
    gate_counts[rejected if rejected in gate_counts else "passed"] += 1
    if rejected:
        # 声の無い・小さすぎる区切り。文字にしない（雑音から「うん。」などを作って送らない）
        raise HTTPException(status_code=422, detail="音声を認識できませんでした")
    return result


async def _transcribe_sherpa(engine: str, wav: Path, cwd: Path, meta: dict | None = None) -> str:
    result = await _run_sherpa(engine, wav, cwd)
    if meta is not None and result.get("language"):
        meta["language"] = result["language"]   # 日英の自動で判定した言語
    return str(result.get("text") or "").strip()


@router.get("/status")
def get_status(user: User = Depends(require_permission("workflows.run"))):
    return status()


@router.post("/install-jobs", status_code=201)
async def install_job(request: Request, user: User = Depends(require_permission("workflows.run")), db=Depends(get_db)):
    global _install_job_id
    current = jobs.get(_install_job_id) if _install_job_id else None
    if current and current.status in ("queued", "running"):
        return {"job_id": current.id, "reused": True}
    job = jobs.create("asr.install", "音声入力モデルを導入", install,
                      owner_user_id=user.id, priority=-5)
    _install_job_id = job.id
    audit.record(db, "asr.install", user=user, resource_type="runtime", resource_id="Haas-ja-v1.0",
                 request=request, metadata={"job_id": job.id, "model": "Haas-ja-v1.0", "revision": HAAS_REPO.rsplit("/", 1)[-1]})
    return {"job_id": job.id, "reused": False}


async def _exec_capture(*args: str, cwd: Path, timeout: float) -> tuple[int, str]:
    process = await asyncio.create_subprocess_exec(
        *args, cwd=str(cwd), stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=timeout)
    except TimeoutError:
        process.kill()
        await process.wait()
        raise RuntimeError("音声認識が時間内に完了しませんでした")
    diagnostics = (stderr or stdout).decode("utf-8", errors="replace")[-2000:]
    return process.returncode or 0, diagnostics


def speed_args(wav_bytes: int) -> list[str]:
    """短い区切りを速く文字にする設定。

    whisper は音声を 30 秒に詰め物して読むので、6 秒の区切りでも 14〜17 秒かかっていた（2026-11-18 実測・CPU）。
    読む範囲（audio_ctx。50 で 1 秒）を区切りの長さ＋余裕に絞ると約 3 秒になる。スレッドは 8 が最も速かった
    （16 は遅い）。長さは 16 kHz・16 bit・1 ch の WAV の大きさから出す。"""
    seconds = max(0.0, (wav_bytes - 44) / 32000)
    # 下限 768: 256〜512 に絞ると 3 秒の区切りで「私は私は私は」と繰り返し、無い言葉も出た（2026-11-18 本番・再現済み）
    ctx = min(1500, max(768, int((seconds + 2.0) * 50 + 63) // 64 * 64))
    threads = max(1, min(8, os.cpu_count() or 4))
    args = ["-t", str(threads)]
    if ctx < 1500:
        args += ["-ac", str(ctx)]
    return args


_REPEATED_PHRASE = re.compile(r"(.{4,80}?)(?:[、。，．,.!?！？\s]*\1)+", re.S)
_WHISPER_PHANTOMS = {"ご視聴ありがとうございました", "ご視聴ありがとうございました。", "チャンネル登録お願いします"}


def clean_transcript(text: str) -> str:
    """同じ言い回しの連続を 1 回にする（whisper の繰り返し対策。Haas でも念のため通す）。"""
    return _REPEATED_PHRASE.sub(r"\1", text.strip()).strip()


async def _transcribe_with(engine: str, wav: Path, request_dir: Path, output: Path, meta: dict | None = None) -> str:
    """1 つのエンジンで文字にする。認識できない（無音など・フィルターで捨てた）は 422、エンジンの失敗は例外。"""
    if engine in ("haas", "parakeet-en", "auto-ja-en"):
        text = clean_transcript(await _transcribe_sherpa(engine, wav, request_dir, meta))
        if not text:
            raise HTTPException(status_code=422, detail="音声を認識できませんでした")
        return text
    if sherpa_ready() and filter_ready():
        await _run_sherpa(None, wav, request_dir)   # whisper の前に門だけ通す（whisper は雑音から文を作りやすい）
    code, diagnostics = await _exec_capture(
        str(binary_path()), "-m", str(model_path()), "-f", str(wav),
        "-l", whisper_language(), "-nt", "-np", "-otxt", "-of", str(output), *speed_args(wav.stat().st_size),
        cwd=request_dir, timeout=120,
    )
    transcript_file = output.with_suffix(".txt")
    if code != 0 or not transcript_file.is_file():
        logger.warning("ASR transcription failed: %s", diagnostics)
        raise RuntimeError(f"whisper が終了コード {code} で失敗しました")
    text = clean_transcript(transcript_file.read_text(encoding="utf-8", errors="replace"))
    if not text or text in ("[BLANK_AUDIO]", "[無音]") or text in _WHISPER_PHANTOMS:
        raise HTTPException(status_code=422, detail="音声を認識できませんでした")
    return text


@router.post("/transcribe")
async def transcribe(
    audio: UploadFile = File(...),
    user: User = Depends(require_permission("workflows.run")),
):
    if not is_ready():
        raise HTTPException(status_code=409, detail="音声入力モデルが未導入です")
    media_type = (audio.content_type or "").lower()
    if media_type and not (media_type.startswith("audio/") or media_type == "application/octet-stream"):
        raise HTTPException(status_code=422, detail="音声ファイル形式ではありません")

    temp_root = (data_dir() / "tmp" / "asr").resolve()
    request_dir = _inside(temp_root / uuid.uuid4().hex, temp_root)
    request_dir.mkdir(parents=True, exist_ok=False)
    source = _inside(request_dir / "input.audio", temp_root)
    wav = _inside(request_dir / "input.wav", temp_root)
    output = _inside(request_dir / "transcript", temp_root)
    try:
        received = 0
        with source.open("wb") as target:
            while chunk := await audio.read(1024 * 1024):
                received += len(chunk)
                if received > MAX_AUDIO_BYTES:
                    raise HTTPException(status_code=413, detail="音声は25MiB以内にしてください")
                target.write(chunk)
        if received == 0:
            raise HTTPException(status_code=422, detail="音声データが空です")
        ffmpeg = shutil.which("ffmpeg")
        if not ffmpeg:
            raise HTTPException(status_code=503, detail="音声変換に必要なffmpegがありません")
        from app.speech import settings

        flt = settings.input_filter()
        # 低い音（うなり・空調）のカット。入力フィルターの詳細で選んだときだけ
        shaping = ["-af", "highpass=f=80"] if flt["enabled"] and flt["highpass"] else []
        code, diagnostics = await _exec_capture(
            ffmpeg, "-nostdin", "-hide_banner", "-loglevel", "error", "-y",
            "-i", str(source), *shaping, "-ar", "16000", "-ac", "1", "-c:a", "pcm_s16le", str(wav),
            cwd=request_dir, timeout=45,
        )
        if code != 0 or not wav.is_file():
            logger.warning("ASR audio conversion failed: %s", diagnostics)
            raise HTTPException(status_code=422, detail="音声を変換できませんでした")
        global last_fallback, fallback_worked
        order = engine_order()
        if not order:
            raise HTTPException(status_code=409, detail="選んだ音声認識が未導入です（Models の TTS / ASR から導入）")
        for index, engine in enumerate(order):
            meta: dict = {}
            try:
                text = await _transcribe_with(engine, wav, request_dir, output, meta)
            except HTTPException:
                raise
            except Exception as exc:  # noqa: BLE001 - 控えがあれば入力を止めない
                last_fallback, fallback_worked = f"{type(exc).__name__}: {exc}"[:300], False
                logger.warning("%s に失敗しました: %s", engine, last_fallback)
                if index == len(order) - 1:
                    raise HTTPException(status_code=502, detail="音声認識に失敗しました") from exc
                continue
            if index == 0:
                last_fallback, fallback_worked = "", False
            else:
                fallback_worked = True
            models = {"haas": "Haas-ja-v1.0", "parakeet-en": "Parakeet-TDT-0.6B-v2", "whisper": MODEL_NAME,
                      "auto-ja-en": "Haas-ja-v1.0" if meta.get("language") != "en" else "Parakeet-TDT-0.6B-v2"}
            language = meta.get("language") or ENGINE_LANGUAGE.get(engine, whisper_language())
            return {"text": text, "language": language, "model": models[engine]}
        raise HTTPException(status_code=502, detail="音声認識に失敗しました")
    finally:
        await audio.close()
        shutil.rmtree(request_dir, ignore_errors=True)
