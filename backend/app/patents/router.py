"""Patent（特許調査・検討）API。/api/v1/patents。

閲覧は workflows.run、書き換え・AI 支援・取り込みは workflows.edit。案件の作成・削除・AI 支援・文献の取り込みは監査に残す
（本文や請求項は監査に書かない）。重い処理（AI 支援・文献の取り込み）は Jobs で行う。
"""
from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from app.audit import service as audit
from app.database import get_db
from app.models import User
from app.patents import domain, service
from app.patents import store as pstore
from app.security.deps import require_permission

router = APIRouter(prefix="/patents", tags=["patents"])
LITERATURE_COLLECTION = "patent-literature"

VIEW = Depends(require_permission("workflows.run"))
EDIT = require_permission("workflows.edit")


def _call(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except pstore.NotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except pstore.PatentError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


class CaseCreate(BaseModel):
    title: str = Field(min_length=1, max_length=300)
    data: dict = {}


class CasePatch(BaseModel):
    title: str | None = Field(default=None, max_length=300)
    stage: str | None = None
    data: dict | None = None


class EventBody(BaseModel):
    kind: str
    date: str = Field(min_length=10, max_length=10)
    note: str = Field(default="", max_length=2000)


class ClaimsBody(BaseModel):
    text: str = Field(min_length=1, max_length=200_000)
    note: str = Field(default="", max_length=1000)


class RefBody(BaseModel):
    ref: str = Field(default="", max_length=300)
    doc_ref: str = Field(default="", max_length=80)
    category: str = "A"
    relevance: str = Field(default="", max_length=2000)
    note: str = Field(default="", max_length=2000)


class SearchBody(BaseModel):
    query: str = Field(min_length=1, max_length=2000)
    limit: int = Field(default=20, ge=1, le=50)
    mode: str = Field(default="hybrid", pattern="^(hybrid|fulltext|vector)$")


class ChartBody(BaseModel):
    claim_no: int = Field(default=1, ge=1, le=200)
    per_element: int = Field(default=3, ge=1, le=8)


class HearingBody(BaseModel):
    text: str = Field(default="", max_length=200_000)
    replace: bool = False


class AssistBody(BaseModel):
    task: str
    extra: str = Field(default="", max_length=20_000)


class ReviewBody(BaseModel):
    review_status: str


class LiteratureBody(BaseModel):
    title: str = Field(default="", max_length=300)
    text: str = Field(default="", max_length=2_000_000)
    url: str = Field(default="", max_length=2000)
    path: str = Field(default="", max_length=4096)
    public: bool = False


@router.get("/meta")
async def meta(user: User = VIEW):
    try:
        await service.resolve_local_target()
        local_ai = True
    except service.LocalAIUnavailable:
        local_ai = False
    return {"stages": domain.STAGES, "event_kinds": domain.EVENT_KINDS, "ref_categories": domain.REF_CATEGORIES,
            "tasks": {k: v["label"] for k, v in domain.PROMPTS.items()}, "local_ai_available": local_ai,
            "literature_collection": LITERATURE_COLLECTION}


@router.get("/cases")
def list_cases(user: User = VIEW):
    return pstore.list_cases()


@router.post("/cases", status_code=201)
def create_case(body: CaseCreate, request: Request, user: User = Depends(EDIT), db=Depends(get_db)):
    case = _call(pstore.create_case, body.title, body.data)
    audit.record(db, "patents.case_create", user=user, resource_type="patent_case", resource_id=str(case["id"]),
                 request=request)
    return case


@router.get("/cases/{case_id}")
def get_case(case_id: int, user: User = VIEW):
    return _call(pstore.get_case, case_id)


@router.patch("/cases/{case_id}")
def patch_case(case_id: int, body: CasePatch, user: User = Depends(EDIT)):
    return _call(pstore.update_case, case_id, title=body.title, stage=body.stage, data=body.data)


@router.delete("/cases/{case_id}")
def delete_case(case_id: int, request: Request, user: User = Depends(EDIT), db=Depends(get_db)):
    _call(pstore.delete_case, case_id)
    audit.record(db, "patents.case_delete", user=user, resource_type="patent_case", resource_id=str(case_id),
                 request=request)
    return {"ok": True}


@router.post("/cases/{case_id}/events")
def add_event(case_id: int, body: EventBody, user: User = Depends(EDIT)):
    return _call(pstore.add_event, case_id, body.kind, body.date, body.note)


@router.delete("/cases/{case_id}/events/{event_id}")
def delete_event(case_id: int, event_id: int, user: User = Depends(EDIT)):
    return _call(pstore.delete_event, case_id, event_id)


@router.post("/cases/{case_id}/claims")
def save_claims(case_id: int, body: ClaimsBody, user: User = Depends(EDIT)):
    return _call(pstore.save_claims, case_id, body.text, body.note)


@router.post("/cases/{case_id}/hearing")
def append_hearing(case_id: int, body: HearingBody, user: User = Depends(EDIT)):
    """ヒアリングの記録（音声の文字起こし・手入力）に足す。replace=true なら置き換える（手で直したとき）。"""
    return _call(pstore.append_hearing, case_id, body.text, replace=body.replace)


@router.post("/cases/{case_id}/refs")
def add_ref(case_id: int, body: RefBody, user: User = Depends(EDIT)):
    return _call(pstore.add_ref, case_id, ref=body.ref, doc_ref=body.doc_ref, category=body.category,
                 relevance=body.relevance, note=body.note)


@router.delete("/cases/{case_id}/refs/{ref_id}")
def delete_ref(case_id: int, ref_id: int, user: User = Depends(EDIT)):
    return _call(pstore.delete_ref, case_id, ref_id)


@router.post("/search")
async def search(body: SearchBody, user: User = VIEW):
    return {"results": await service.search(body.query, limit=body.limit, mode=body.mode)}


@router.post("/cases/{case_id}/claim-chart")
async def claim_chart(case_id: int, body: ChartBody, user: User = VIEW):
    try:
        return await service.claim_chart(case_id, body.claim_no, body.per_element)
    except pstore.NotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except pstore.PatentError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/cases/{case_id}/assists", status_code=201)
async def start_assist(case_id: int, body: AssistBody, request: Request, user: User = Depends(EDIT),
                       db=Depends(get_db)):
    from app.jobs import service as jobs

    try:
        await service.resolve_local_target()
    except service.LocalAIUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    assist_id = await asyncio.to_thread(_call, pstore.start_assist, case_id, body.task)

    async def run(job: jobs.Job) -> dict:
        return await service.run_assist(job, case_id, body.task, assist_id, body.extra)

    job = jobs.create("patents.assist", f"特許の AI 支援: {domain.PROMPTS[body.task]['label']}", run,
                      owner_user_id=user.id)
    pstore.finish_assist(assist_id, status="queued", job_id=job.id)
    audit.record(db, "patents.assist", user=user, resource_type="patent_case", resource_id=str(case_id),
                 request=request, metadata={"task": body.task, "job_id": job.id})
    return {"assist_id": assist_id, "job_id": job.id}


@router.post("/cases/{case_id}/assists/{assist_id}/review")
def review_assist(case_id: int, assist_id: int, body: ReviewBody, user: User = Depends(EDIT)):
    return _call(pstore.review_assist, case_id, assist_id, body.review_status)


@router.delete("/cases/{case_id}/assists/{assist_id}")
def delete_assist(case_id: int, assist_id: int, user: User = Depends(EDIT)):
    return _call(pstore.delete_assist, case_id, assist_id)


class BiblioBody(BaseModel):
    fields: dict


@router.get("/analytics")
def analytics_overview(collection: str | None = None, user: User = VIEW):
    """特許の分析（年別・出願人・IPC・出願人×IPC・出願人×年・請求項の数・国と種別）。書誌の読めた件数も返す。"""
    from app.patents import analytics

    return analytics.overview(collection)


@router.get("/analytics/map")
def analytics_map(collection: str | None = None, user: User = VIEW):
    """類似マップ（公報の内容のベクトルを 2 次元に）。"""
    from app.patents import analytics

    return analytics.similarity_map(collection)


@router.post("/analytics/refresh-biblio", status_code=201)
async def refresh_biblio(request: Request, user: User = Depends(EDIT), db=Depends(get_db)):
    """書誌の無い公報を読み直す（Jobs。1 ページ目の OCR を含む）。"""
    from app.jobs import service as jobs
    from app.patents import analytics

    job = jobs.create("patents.biblio", "公報の書誌を読み直す", lambda j: analytics.refresh_biblio(j), owner_user_id=user.id)
    audit.record(db, "patents.refresh_biblio", user=user, resource_type="knowledge", resource_id="patents", request=request,
                 metadata={"job_id": job.id})
    return {"job_id": job.id}


@router.patch("/documents/{ref}/biblio")
def patch_biblio(ref: str, body: BiblioBody, user: User = Depends(EDIT)):
    """書誌を手で直す（OCR の読み違いなど）。"""
    from app.knowledge import store as kstore
    from app.patents import analytics

    try:
        return analytics.set_biblio(ref, body.fields)
    except kstore.KnowledgeError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/cases/{case_id}/similar")
async def similar(case_id: int, user: User = VIEW):
    """案件の請求項（無ければ課題・解決手段）に近い公報。分析画面と先行文献の候補に使う。"""
    from app.knowledge import documents as kdocs

    case = await asyncio.to_thread(_call, pstore.get_case, case_id)
    claims = case["claims"][0]["text"] if case["claims"] else case["data"].get("claims_draft", "")
    text = claims or " ".join(str(case["data"].get(k, "")) for k in ("problem", "solution")) or case["title"]
    query = " ".join(domain.keywords(text)[:12]) or case["title"]
    hits = await service.search(query, limit=10)
    out = []
    for h in hits:
        doc = await asyncio.to_thread(kdocs.get_document, h["doc_ref"])
        b = doc["meta"].get("biblio") or {}
        out.append({"ref": h["doc_ref"], "title": h["title"], "score": h["score"], "snippet": h["snippet"],
                    "applicants": b.get("applicants") or [], "ipc": b.get("ipc") or [],
                    "date": b.get("publication_date") or b.get("filing_date") or "",
                    "registered": any(r["doc_ref"] == h["doc_ref"] for r in case["refs"])})
    return {"query": query, "results": out}


@router.get("/literature")
def literature(user: User = VIEW):
    from app.workflows import rag

    return {"collection": LITERATURE_COLLECTION,
            "documents": rag.list_documents(LITERATURE_COLLECTION) if rag.collection_exists(LITERATURE_COLLECTION) else []}


@router.post("/literature", status_code=201)
async def add_literature(body: LiteratureBody, request: Request, user: User = Depends(EDIT), db=Depends(get_db)):
    """公報・論文などを特許の分野（patents.sqlite3）へ取り込む。既定は機密。公開済みの公報だけ public にできる。"""
    from app.jobs import service as jobs
    from app.knowledge import store as kstore
    from app.workflows import rag

    if not (body.text.strip() or body.url.strip() or body.path.strip()):
        raise HTTPException(status_code=422, detail="取り込むテキスト・URL・ファイルのいずれかを指定してください")
    if await asyncio.to_thread(kstore.find_collection, LITERATURE_COLLECTION) is None:
        await asyncio.to_thread(kstore.create_collection, LITERATURE_COLLECTION, shard="patents", kind="patent",
                                sensitivity="confidential", config=dict(rag.DEFAULT_CONFIG),
                                description="Patent 機能で取り込んだ公報・論文")

    async def run(job: jobs.Job) -> dict:
        text, source = body.text, body.title
        if body.url:
            job.set_progress("URLを取得中", 0, 3)
            from bs4 import BeautifulSoup

            from app.workflows import scrape_tools as st

            html, _, final = await st.fetch(body.url)
            text = BeautifulSoup(html, "html.parser").get_text("\n", strip=True)
            source = source or final
        elif body.path:
            job.set_progress("ファイルを読み込み中", 0, 3)
            from app.files.service import read_text

            text = await asyncio.to_thread(read_text, body.path)
            source = source or body.path
        if not text.strip():
            raise rag.RagError("取り込むテキストがありません")
        job.set_progress("分割・埋め込み中", 1, 3)
        out = await rag.add_document(LITERATURE_COLLECTION, text, source or "document")
        if body.public:
            await asyncio.to_thread(_mark_public, out["doc_id"])
        job.set_progress("完了", 3, 3)
        return out

    job = jobs.create("patents.literature", f"特許文献の取り込み: {body.title or body.url or body.path or 'テキスト'}"[:120],
                      run, owner_user_id=user.id)
    audit.record(db, "patents.literature_add", user=user, resource_type="knowledge",
                 resource_id=LITERATURE_COLLECTION, request=request,
                 metadata={"job_id": job.id, "public": body.public, "source": (body.url or body.path or "text")[:200]})
    return {"job_id": job.id}


def _mark_public(doc_id: int) -> None:
    from app.knowledge import store as kstore

    conn = kstore.connect("patents")
    try:
        conn.execute("UPDATE documents SET sensitivity='public' WHERE id=?", (int(doc_id),))
        conn.commit()
    finally:
        conn.close()
