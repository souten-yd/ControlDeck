"""ナレッジ（横断検索・文書・図・規格・PDF の取り込み）API。/api/v1/knowledge/...

既存のコレクション管理（app/workflows/knowledge_router.py）と同じ接頭辞に足す。閲覧は workflows.run、登録は workflows.edit。
画面での閲覧（consumer=human）では機密も見えるが、規格の本文は保存が許されたものしか持っていない。
"""
from __future__ import annotations

import asyncio
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from app.audit import service as audit
from app.database import get_db
from app.knowledge import documents, standards, store
from app.knowledge import standards_license as sl
from app.models import User
from app.security.deps import require_permission

router = APIRouter(prefix="/knowledge", tags=["knowledge"])
VIEW = Depends(require_permission("workflows.run"))
EDIT = require_permission("workflows.edit")
SCOPES = {"all": None, "general": "general", "patents": "patents", "standards": "standards"}
MAX_VIEW_CHARS = 400_000


def _wrap(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except store.KnowledgeError as exc:
        raise HTTPException(status_code=404 if "ありません" in str(exc) else 422, detail=str(exc)) from exc


class FindBody(BaseModel):
    query: str = Field(min_length=1, max_length=2000)
    scope: str = Field(default="all", pattern="^(all|general|patents|standards)$")
    limit: int = Field(default=10, ge=1, le=30)
    include_conversations: bool = False


@router.post("/find")
async def find(body: FindBody, user: User = VIEW):
    """文書を単位に探す（画面の横断検索）。番号の完全一致・書誌の関係（1 hop）も出す。"""
    from app.knowledge import context

    def names() -> list[str]:
        out = []
        for c in store.list_collections():
            if SCOPES[body.scope] and c["shard"] != SCOPES[body.scope]:
                continue
            if c["kind"] == "conversation" and not body.include_conversations:
                continue
            out.append(c["collection"])
        return out

    collections = await asyncio.to_thread(names)
    vector, model = await context.query_vector(body.query)
    ranked = await asyncio.to_thread(
        documents.search_documents, body.query, names=collections, consumer="human", query_vector=vector, model=model,
        mode="hybrid" if vector is not None else "fulltext", limit=body.limit)
    return {"results": [{
        "ref": d["ref"], "title": d["title"], "collection": d["collection"], "relation": d["relation"],
        "via": d.get("via", ""), "sensitivity": d["sensitivity"],
        "passages": [{"text": documents.snippet(h["text"], body.query), "locator": h["locator"]} for h in d["hits"][:3]],
    } for d in ranked]}


@router.get("/documents/{ref}")
def get_document(ref: str, user: User = VIEW):
    doc = _wrap(documents.get_document, ref)
    text = _wrap(documents.document_text, ref)
    doc["text"] = text[:MAX_VIEW_CHARS]
    doc["text_truncated"] = len(text) > MAX_VIEW_CHARS
    doc["related"] = _wrap(documents.related_documents, ref, 12)
    from app.knowledge import analysis

    doc["annotations"] = _wrap(analysis.annotations_with_state, ref)
    doc["analysis_kinds"] = {k: v["label"] for k, v in analysis.KINDS.items()}
    for asset in doc["assets"]:
        asset["url"] = f"/api/v1/knowledge/assets/{asset['sha256']}"
    return doc


@router.get("/assets/{sha256}")
def get_asset(sha256: str, user: User = VIEW):
    """文書に付いた原本・図。ハッシュで指定し、登録された blob だけを返す（任意のパスは開かない）。"""
    info = _wrap(store.blob_info, sha256)
    if info is None:
        raise HTTPException(status_code=404, detail="ありません")
    path = _wrap(store.blob_path, sha256)
    mime = info[1]["mime"]
    if mime not in ("application/pdf", "image/png", "image/jpeg", "image/webp", "application/json"):
        raise HTTPException(status_code=415, detail="表示できない形式です")
    return FileResponse(path, media_type=mime, headers={"Cache-Control": "private, max-age=86400",
                                                        "X-Content-Type-Options": "nosniff"})


class AnalysisBody(BaseModel):
    kind: str = Field(pattern="^(summary|claim_review)$")


class ReviewBody(BaseModel):
    review_status: str = Field(pattern="^(unreviewed|checked|rejected)$")


@router.post("/documents/{ref}/analyses", status_code=201)
async def analyze(ref: str, body: AnalysisBody, request: Request, user: User = Depends(EDIT), db=Depends(get_db)):
    """文献をローカル専用の AI で要約・請求項の確認をする（Jobs）。結果は未確認の推論として注釈に残る。"""
    from app.jobs import service as jobs
    from app.knowledge import analysis
    from app.patents import service as patents_service

    doc = await asyncio.to_thread(_wrap, documents.get_document, ref)
    if doc["ai_use"] == "prohibited":
        raise HTTPException(status_code=422, detail="この文書は利用条件により AI に入力できません")
    try:
        await patents_service.resolve_local_target()
    except patents_service.LocalAIUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    async def run(job: jobs.Job) -> dict:
        return await analysis.run(job, ref, body.kind)

    job = jobs.create("knowledge.analysis", f"文献の{analysis.KINDS[body.kind]['label']}: {doc['title']}"[:120], run,
                      owner_user_id=user.id)
    audit.record(db, "knowledge.analyze", user=user, resource_type="knowledge", resource_id=ref, request=request,
                 metadata={"kind": body.kind, "job_id": job.id})
    return {"job_id": job.id}


@router.post("/documents/{ref}/annotations/{note_id}/review")
def review_annotation(ref: str, note_id: int, body: ReviewBody, user: User = Depends(EDIT)):
    from app.knowledge import analysis

    _wrap(analysis.review, ref, note_id, body.review_status)
    return {"ok": True}


@router.delete("/documents/{ref}/annotations/{note_id}")
def remove_annotation(ref: str, note_id: int, user: User = Depends(EDIT)):
    from app.knowledge import analysis

    _wrap(analysis.remove, ref, note_id)
    return {"ok": True}


class PdfJobBody(BaseModel):
    path: str = Field(min_length=1, max_length=4096)
    title: str = Field(default="", max_length=300)
    describe_figures: bool = True


@router.post("/collections/{name}/pdf-jobs", status_code=201)
async def pdf_job(name: str, body: PdfJobBody, request: Request, user: User = Depends(EDIT), db=Depends(get_db)):
    """PDF を取り込む（文章・OCR・図・図表の説明）。ファイルはファイル管理の許可ルートの中から選ぶ。"""
    from app.files.service import FileAccessError, resolve
    from app.jobs import service as jobs
    from app.knowledge import ingest
    from app.workflows import rag

    try:
        path = resolve(body.path)
    except FileAccessError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    if path.suffix.lower() != ".pdf":
        raise HTTPException(status_code=422, detail="PDF を選んでください")
    if not await asyncio.to_thread(rag.collection_exists, name):
        raise HTTPException(status_code=404, detail="コレクションが見つかりません")

    async def run(job: jobs.Job) -> dict:
        return await ingest.ingest_pdf(name, Path(path), title=body.title, describe_figures=body.describe_figures, job=job)

    job = jobs.create("knowledge.pdf", f"PDF の取り込み: {path.name}"[:120], run, owner_user_id=user.id)
    audit.record(db, "knowledge.add_pdf", user=user, resource_type="knowledge", resource_id=name, request=request,
                 metadata={"job_id": job.id, "file": path.name})
    return {"job_id": job.id}


# ---- 規格 ----

class DecideBody(BaseModel):
    designation: str = Field(min_length=2, max_length=120)
    instrument: str = "purchase"
    permission_ref: str = Field(default="", max_length=120)
    reviewer: str = Field(default="", max_length=80)
    reviewed_on: str = Field(default="", max_length=10)
    # 組織契約・書面許諾で認められた範囲（契約に書かれたとおりに）
    granted_storage: str | None = Field(default=None, pattern="^(metadata_only|personal_copy|shared_server)$")
    granted_ai_use: str | None = Field(default=None, pattern="^(prohibited|local_only|allowed)$")


class StandardBody(DecideBody):
    title: str = Field(default="", max_length=300)
    edition: str = Field(default="", max_length=20)
    status: str = "current"
    text: str = Field(default="", max_length=3_000_000)
    pdf_path: str = Field(default="", max_length=4096)
    note: str = Field(default="", max_length=2000)


class StandardSearchBody(BaseModel):
    query: str = Field(min_length=1, max_length=500)
    include_superseded: bool = False


@router.get("/standards")
def list_standards(user: User = VIEW):
    return {"standards": _wrap(standards.list_standards),
            "instruments": {k: v for k, v in sl.INSTRUMENTS.items() if k != "unknown"}}


@router.post("/standards/decide")
def decide(body: DecideBody, user: User = VIEW):
    """登録する前に、保存・AI 利用の判定と理由を見る（保存しない）。"""
    if body.instrument not in sl.INSTRUMENTS:
        raise HTTPException(status_code=422, detail="入手経路が不正です")
    return standards.decide(body.designation, body.instrument, permission_ref=body.permission_ref,
                            reviewer=body.reviewer, reviewed_on=body.reviewed_on, storage=body.granted_storage,
                            ai_use=body.granted_ai_use).to_dict()


@router.post("/standards", status_code=201)
async def add_standard(body: StandardBody, request: Request, user: User = Depends(EDIT), db=Depends(get_db)):
    from app.files.service import FileAccessError, resolve
    from app.jobs import service as jobs

    if body.instrument not in sl.INSTRUMENTS:
        raise HTTPException(status_code=422, detail="入手経路が不正です")
    pdf_path = ""
    if body.pdf_path:
        try:
            pdf_path = str(resolve(body.pdf_path))
        except FileAccessError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc

    async def run(job: jobs.Job) -> dict:
        return await standards.register(
            designation=body.designation, title=body.title, edition=body.edition, status=body.status,
            instrument=body.instrument, text=body.text, pdf_path=pdf_path, permission_ref=body.permission_ref,
            reviewer=body.reviewer, reviewed_on=body.reviewed_on, granted_storage=body.granted_storage,
            granted_ai_use=body.granted_ai_use, note=body.note, job=job)

    try:
        standards._own(body.designation, body.edition)   # 番号が読めないものは受け付けない
    except store.KnowledgeError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    job = jobs.create("knowledge.standard", f"規格の登録: {body.designation}"[:120], run, owner_user_id=user.id)
    audit.record(db, "knowledge.add_standard", user=user, resource_type="standard", resource_id=body.designation,
                 request=request, metadata={"job_id": job.id, "instrument": body.instrument})
    return {"job_id": job.id}


@router.post("/standards/search")
def search_standards(body: StandardSearchBody, user: User = VIEW):
    return {"results": _wrap(standards.search, body.query, include_superseded=body.include_superseded)}


@router.delete("/standards/{ref}")
def remove_standard(ref: str, request: Request, user: User = Depends(EDIT), db=Depends(get_db)):
    _wrap(standards.remove, ref)
    audit.record(db, "knowledge.remove_standard", user=user, resource_type="standard", resource_id=ref, request=request)
    return {"ok": True}


# ---- 作り直し（AI の回答の区別・埋め込み・PDF の取り込み直し） ----

@router.get("/rebuild")
def rebuild_plan(user: User = Depends(require_permission("settings.manage"))):
    """何を作り直すかの計画（書き込まない）。"""
    from app.knowledge import rebuild

    todo = rebuild.plan()
    return {k: len(v) for k, v in todo.items()}


class RebuildBody(BaseModel):
    describe_figures: bool = True


@router.post("/rebuild", status_code=201)
async def rebuild_apply(body: RebuildBody, request: Request, user: User = Depends(require_permission("settings.manage")),
                        db=Depends(get_db)):
    from app.jobs import service as jobs
    from app.knowledge import rebuild

    job = jobs.create("knowledge.rebuild", "ナレッジの作り直し",
                      lambda j: rebuild.apply_job(j, describe_figures=body.describe_figures), owner_user_id=user.id)
    audit.record(db, "knowledge.rebuild", user=user, resource_type="knowledge", resource_id="all", request=request,
                 metadata={"job_id": job.id, "describe_figures": body.describe_figures})
    return {"job_id": job.id}
