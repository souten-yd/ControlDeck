"""AIチャット用ローカルASR（whisper.cppのオンデマンド導入・再利用）。"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import shutil
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


def is_ready() -> bool:
    model = model_path()
    return _binary_ready() and model.is_file() and model.stat().st_size == MODEL_BYTES


def status() -> dict:
    active = jobs.get(_install_job_id) if _install_job_id else None
    state = "ready" if is_ready() else (
        "installing" if active and active.status in ("queued", "running") else "missing"
    )
    return {
        "state": state,
        "ready": state == "ready",
        "installing": state == "installing",
        "job_id": active.id if active and active.status in ("queued", "running") else None,
        "runtime": f"whisper.cpp {WHISPER_VERSION}",
        "model": MODEL_NAME,
        "model_bytes": model_path().stat().st_size if model_path().is_file() else 0,
        "storage": str(runtime_root()),
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


async def install(job: jobs.Job) -> dict:
    """固定タグのwhisper.cppと固定hashの多言語baseモデルを導入する。"""
    root = runtime_root()
    binary = binary_path()
    model = model_path()
    if is_ready():
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
        part = _inside(model.with_suffix(".bin.part"), root)
        part.unlink(missing_ok=True)
        digest = hashlib.sha256()
        received = 0
        job.set_progress("音声認識モデルを取得中", 2, 3)
        try:
            async with httpx.AsyncClient(timeout=None, follow_redirects=True,
                                         headers={"User-Agent": "ControlDeck"}) as client:
                async with client.stream("GET", MODEL_URL) as response:
                    response.raise_for_status()
                    with part.open("wb") as target:
                        async for chunk in response.aiter_bytes(1024 * 1024):
                            target.write(chunk)
                            digest.update(chunk)
                            received += len(chunk)
                            job.set_progress("音声認識モデルを取得中", received, MODEL_BYTES)
            if received != MODEL_BYTES or digest.hexdigest() != MODEL_SHA256:
                raise RuntimeError("音声認識モデルのサイズまたはSHA-256が一致しません")
            part.replace(model)
        except BaseException:
            part.unlink(missing_ok=True)
            raise

    job.set_progress("ASRを利用できます", 3, 3)
    if not is_ready():
        raise RuntimeError("ASR導入後の検証に失敗しました")
    return status()


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
    audit.record(db, "asr.install", user=user, resource_type="runtime", resource_id=WHISPER_VERSION,
                 request=request, metadata={"job_id": job.id, "model": MODEL_NAME})
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
        code, diagnostics = await _exec_capture(
            str(binary_path()), "-m", str(model_path()), "-f", str(wav),
            "-l", "ja", "-nt", "-np", "-otxt", "-of", str(output),
            cwd=request_dir, timeout=120,
        )
        transcript_file = output.with_suffix(".txt")
        if code != 0 or not transcript_file.is_file():
            logger.warning("ASR transcription failed: %s", diagnostics)
            raise HTTPException(status_code=502, detail="音声認識に失敗しました")
        text = transcript_file.read_text(encoding="utf-8", errors="replace").strip()
        if not text or text in ("[BLANK_AUDIO]", "[無音]"):
            raise HTTPException(status_code=422, detail="音声を認識できませんでした")
        return {"text": text, "language": "ja", "model": MODEL_NAME}
    finally:
        await audio.close()
        shutil.rmtree(request_dir, ignore_errors=True)
