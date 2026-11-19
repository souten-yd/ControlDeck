"""TTS / ASR のエンジン一覧・選択・導入・合成の API。Models の「TTS / ASR」タブが使う。"""
from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, Field

from app.audit import service as audit
from app.database import get_db
from app.jobs import service as jobs
from app.models import User
from app.security.deps import require_permission
from app.speech import settings, supertonic
from app.workflows import asr

router = APIRouter(prefix="/speech", tags=["speech"])
_asr_jobs: dict[str, str] = {}


def _asr_engine(engine: str) -> dict:
    job = jobs.get(_asr_jobs[engine]) if engine in _asr_jobs else None
    installing = bool(job and job.status in ("queued", "running"))
    if engine == "haas":
        ready = asr.haas_ready()
        return {
            "id": "haas", "name": "Haas-ja v1.0", "kind": "asr", "ready": ready, "installed": ready,
            "installing": installing, "job_id": job.id if installing and job else None,
            "runtime": "sherpa-onnx 2.0.1（CPU）", "model": "Haas-ja-v1.0（量子化）",
            "model_bytes": sum(size for size, _ in asr.HAAS_FILES.values()), "storage": str(asr.haas_root()),
            "summary": "日本語専用。whisper より約 8 倍速く、句読点も付く（既定）",
            "error": asr.last_install_error if not ready else "",
        }
    ready = asr.whisper_ready()
    return {
        "id": "whisper", "name": f"whisper.cpp {asr.MODEL_NAME}", "kind": "asr", "ready": ready, "installed": ready,
        "installing": installing, "job_id": job.id if installing and job else None,
        "runtime": f"whisper.cpp {asr.WHISPER_VERSION}（CPU）", "model": asr.MODEL_NAME,
        "model_bytes": asr.MODEL_BYTES, "storage": str(asr.runtime_root()),
        "summary": "多言語。導入時にソースからビルドする（git と cmake が要る）",
        "error": "",
    }


def engines() -> dict:
    tts = supertonic.status()
    tts["summary"] = "31 言語の多言語 TTS。CPU で実時間の十数倍速。上流は開発終了のため保管庫から導入する"
    return {
        "selected": settings.load(),
        "engines": [_asr_engine("haas"), _asr_engine("whisper"), tts],
        "active_asr": asr.engine_order()[0] if asr.is_ready() else None,
    }


@router.get("/engines")
def list_engines(user: User = Depends(require_permission("workflows.run"))):
    return engines()


class EngineSelection(BaseModel):
    model_config = ConfigDict(extra="forbid")
    asr: Literal[settings.ASR_ENGINES] | None = None  # type: ignore[valid-type]
    tts: Literal[settings.TTS_ENGINES] | None = None  # type: ignore[valid-type]


@router.put("/engines")
def select_engines(body: EngineSelection, request: Request,
                   user: User = Depends(require_permission("settings.manage")), db=Depends(get_db)):
    values = {kind: value for kind, value in body.model_dump().items() if value is not None}
    selected = settings.save(values)
    audit.record(db, "speech.engines.select", user=user, resource_type="settings", resource_id="speech-engines",
                 request=request, metadata=values)
    return engines() | {"selected": selected}


@router.post("/engines/{engine_id}/install-jobs", status_code=201)
async def install_engine(engine_id: str, request: Request,
                         user: User = Depends(require_permission("settings.manage")), db=Depends(get_db)):
    if engine_id == supertonic.ENGINE_ID:
        job = supertonic.start_install_job(user.id)
    elif engine_id in ("haas", "whisper"):
        current = jobs.get(_asr_jobs[engine_id]) if engine_id in _asr_jobs else None
        if current and current.status in ("queued", "running"):
            return {"job_id": current.id, "reused": True}

        async def run(job: jobs.Job) -> dict:
            if engine_id == "haas":
                await asr.install_haas(job)
                if not asr.haas_ready():
                    raise RuntimeError("Haas-ja の導入後の確認に失敗しました")
            else:
                await asr.install_whisper(job)
            return _asr_engine(engine_id)

        label = "Haas-ja（ASR）" if engine_id == "haas" else "whisper.cpp（ASR）"
        job = jobs.create("speech.install", f"{label}を導入", run, owner_user_id=user.id, priority=-5)
        _asr_jobs[engine_id] = job.id
    else:
        raise HTTPException(status_code=404, detail="未知の音声エンジンです")
    audit.record(db, "speech.install", user=user, resource_type="runtime", resource_id=engine_id,
                 request=request, metadata={"job_id": job.id})
    return {"job_id": job.id, "reused": False}


class SpeechRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str = Field(min_length=1, max_length=supertonic.MAX_TEXT_CHARS)
    voice: Literal[supertonic.VOICES] = "F1"  # type: ignore[valid-type]
    lang: Literal[supertonic.LANGUAGES] = "ja"  # type: ignore[valid-type]
    speed: float = Field(default=1.05, ge=0.7, le=2.0)
    steps: int = Field(default=8, ge=1, le=32)


@router.post("/tts")
async def text_to_speech(body: SpeechRequest, user: User = Depends(require_permission("workflows.run"))):
    """選んだ TTS エンジンで WAV を返す。"""
    engine = settings.selected("tts")
    if engine != supertonic.ENGINE_ID or not supertonic.ready():
        raise HTTPException(status_code=409, detail="TTS エンジンが未導入です（Models の TTS / ASR から導入）")
    try:
        audio = await supertonic.synthesize(body.text, voice=body.voice, lang=body.lang,
                                            speed=body.speed, steps=body.steps)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except supertonic.SupertonicError as exc:
        raise HTTPException(status_code=502, detail="音声合成に失敗しました") from exc
    return Response(content=audio, media_type="audio/wav",
                    headers={"Cache-Control": "no-store", "X-Speech-Engine": engine})
