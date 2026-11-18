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
_haas_gate = asyncio.Semaphore(1)     # 1 回 約 0.86 GiB。同時に動かさない
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


def whisper_ready() -> bool:
    model = model_path()
    return _binary_ready() and model.is_file() and model.stat().st_size == MODEL_BYTES


def is_ready() -> bool:
    return haas_ready() or whisper_ready()


def status() -> dict:
    active = jobs.get(_install_job_id) if _install_job_id else None
    haas = haas_ready()
    state = "ready" if is_ready() else (
        "installing" if active and active.status in ("queued", "running") else "missing"
    )
    return {
        "state": state,
        "ready": state == "ready",
        "installing": state == "installing",
        "job_id": active.id if active and active.status in ("queued", "running") else None,
        "runtime": "sherpa-onnx 2.0.1" if haas else f"whisper.cpp {WHISPER_VERSION}",
        "model": "Haas-ja-v1.0" if haas else MODEL_NAME,
        "engine": "haas" if haas else "whisper",
        # whisper で使えるが Haas がまだ無い: 裏で導入する（待たせない。失敗したらしばらく試さない）
        "upgrade_available": state == "ready" and not haas and time.time() >= _install_retry_at,
        "install_error": last_install_error,
        "model_bytes": sum(size for size, _ in HAAS_FILES.values()) if haas
        else model_path().stat().st_size if model_path().is_file() else 0,
        "storage": str(haas_root() if haas else runtime_root()),
        "reusable": is_ready(),
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


async def install_haas(job: jobs.Job) -> None:
    """Haas-ja（版固定のモデル・ハッシュ照合）と専用の venv を導入する。"""
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


async def install(job: jobs.Job) -> dict:
    """音声認識（Haas-ja）を導入する。入れられなければ whisper を控えとして使う（無ければ導入する）。"""
    global last_install_error, _install_retry_at
    if not haas_ready():
        try:
            await install_haas(job)
            last_install_error = ""
        except Exception as exc:  # noqa: BLE001
            last_install_error = f"{type(exc).__name__}: {exc}"[:300]
            _install_retry_at = time.time() + INSTALL_RETRY_SECONDS
            job.log(f"Haas-ja を導入できませんでした: {last_install_error}")
            if not whisper_ready():
                await install_whisper(job)
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


async def _transcribe_haas(wav: Path, cwd: Path) -> str:
    worker = Path(__file__).with_name("asr_haas_worker.py")
    async with _haas_gate:
        process = await asyncio.create_subprocess_exec(
            str(haas_python()), "-I", str(worker), str(haas_root() / "model"), str(wav), str(HAAS_THREADS),
            cwd=str(cwd), stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        try:
            stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=90)
        except TimeoutError:
            raise RuntimeError("音声認識が時間内に完了しませんでした")
        finally:
            if process.returncode is None:   # 時間切れ・取り消しでも残さない（同時に 1 つを守る）
                process.kill()
                await process.wait()
    if process.returncode != 0:
        raise RuntimeError(stderr.decode("utf-8", errors="replace")[-300:] or "Haas-ja が失敗しました")
    return str(json.loads(stdout.decode("utf-8").strip().splitlines()[-1]).get("text") or "").strip()


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
        code, diagnostics = await _exec_capture(
            ffmpeg, "-nostdin", "-hide_banner", "-loglevel", "error", "-y",
            "-i", str(source), "-ar", "16000", "-ac", "1", "-c:a", "pcm_s16le", str(wav),
            cwd=request_dir, timeout=45,
        )
        if code != 0 or not wav.is_file():
            logger.warning("ASR audio conversion failed: %s", diagnostics)
            raise HTTPException(status_code=422, detail="音声を変換できませんでした")
        if haas_ready():
            global last_fallback, fallback_worked
            try:
                text = clean_transcript(await _transcribe_haas(wav, request_dir))
                last_fallback, fallback_worked = "", False
                if not text:
                    raise HTTPException(status_code=422, detail="音声を認識できませんでした")
                return {"text": text, "language": "ja", "model": "Haas-ja-v1.0"}
            except HTTPException:
                raise
            except Exception as exc:  # noqa: BLE001 - whisper があれば入力を止めない
                last_fallback, fallback_worked = f"{type(exc).__name__}: {exc}"[:300], False
                logger.warning("Haas-ja に失敗しました: %s", last_fallback)
                if not whisper_ready():
                    raise HTTPException(status_code=502, detail="音声認識に失敗しました") from exc
        code, diagnostics = await _exec_capture(
            str(binary_path()), "-m", str(model_path()), "-f", str(wav),
            "-l", "ja", "-nt", "-np", "-otxt", "-of", str(output), *speed_args(wav.stat().st_size),
            cwd=request_dir, timeout=120,
        )
        transcript_file = output.with_suffix(".txt")
        if code != 0 or not transcript_file.is_file():
            logger.warning("ASR transcription failed: %s", diagnostics)
            raise HTTPException(status_code=502, detail="音声認識に失敗しました")
        text = clean_transcript(transcript_file.read_text(encoding="utf-8", errors="replace"))
        if not text or text in ("[BLANK_AUDIO]", "[無音]") or text in _WHISPER_PHANTOMS:
            raise HTTPException(status_code=422, detail="音声を認識できませんでした")
        fallback_worked = bool(last_fallback)
        return {"text": text, "language": "ja", "model": MODEL_NAME}
    finally:
        await audio.close()
        shutil.rmtree(request_dir, ignore_errors=True)
