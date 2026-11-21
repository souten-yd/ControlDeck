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


class JudgeBody(BaseModel):
    claim_no: int = Field(default=1, ge=1, le=200)
    doc_ref: str = Field(min_length=3, max_length=80)


class JudgeRowBody(BaseModel):
    verdict: str = Field(default="", max_length=2)
    note: str = Field(default="", max_length=1000)


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


@router.get("/cases/{case_id}/charts")
def list_charts(case_id: int, user: User = VIEW):
    """AI クレームチャートの判定（新しい順。請求項・文献の版が変わったものに stale）。"""
    from app.patents import judge

    _call(pstore.get_case, case_id)
    return {"charts": judge.list_charts(case_id)}


@router.post("/cases/{case_id}/charts", status_code=201)
async def start_chart(case_id: int, body: JudgeBody, request: Request, user: User = Depends(EDIT), db=Depends(get_db)):
    """請求項 N と 1 件の文献を構成要件ごとに AI で判定する（ローカル専用・Jobs）。"""
    from app.jobs import service as jobs
    from app.knowledge import store as kstore
    from app.patents import judge

    try:
        await service.resolve_local_target()
    except service.LocalAIUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    try:
        chart_id = await asyncio.to_thread(judge.start, case_id, body.claim_no, body.doc_ref)
    except kstore.KnowledgeError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except pstore.NotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except pstore.PatentError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    async def run(job: jobs.Job) -> dict:
        return await judge.run(job, case_id, chart_id)

    job = jobs.create("patents.claim_judge", f"クレームチャートの判定: 請求項{body.claim_no}", run, owner_user_id=user.id)
    judge._update(chart_id, status="queued", job_id=job.id)
    audit.record(db, "patents.claim_judge", user=user, resource_type="patent_case", resource_id=str(case_id),
                 request=request, metadata={"claim_no": body.claim_no, "doc_ref": body.doc_ref, "job_id": job.id})
    return {"chart_id": chart_id, "job_id": job.id}


@router.patch("/cases/{case_id}/charts/{chart_id}/rows/{label}")
def set_chart_row(case_id: int, chart_id: int, label: str, body: JudgeRowBody, user: User = Depends(EDIT)):
    from app.patents import judge

    return _call(judge.set_row, case_id, chart_id, label, body.verdict, body.note)


@router.post("/cases/{case_id}/charts/{chart_id}/review")
def review_chart(case_id: int, chart_id: int, body: ReviewBody, user: User = Depends(EDIT)):
    from app.patents import judge

    return _call(judge.review, case_id, chart_id, body.review_status)


@router.delete("/cases/{case_id}/charts/{chart_id}", status_code=204)
def delete_chart(case_id: int, chart_id: int, user: User = Depends(EDIT)):
    from app.patents import judge

    _call(judge.delete, case_id, chart_id)


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
# ---- 公開データの取得・ウォッチ（R3-2） ----

class SourceKeys(BaseModel):
    values: dict[str, str]


class FetchBody(BaseModel):
    query: str = Field(min_length=1, max_length=500)
    limit: int = Field(default=25, ge=1, le=100)
    approved_public: bool = False


class WatchBody(BaseModel):
    source: str
    query: str = Field(min_length=1, max_length=500)
    interval_hours: int = Field(default=168, ge=6, le=720)
    approved_public: bool = False
    enabled: bool = True


class HitBody(BaseModel):
    status: str = Field(pattern="^(new|relevant|excluded|registered)$")
    category: str | None = Field(default=None, pattern="^[XYAPE]$")


def _need_public(flag: bool) -> None:
    if not flag:
        raise HTTPException(status_code=422, detail="外部へ送る検索語です。未公開の発明の内容を含まないことを確認してください")


@router.get("/sources")
def list_sources(user: User = VIEW):
    from app.knowledge import sources

    return {"sources": sources.listing()}


@router.put("/sources/keys")
def set_source_keys(body: SourceKeys, request: Request, user: User = Depends(require_permission("settings.manage")),
                    db=Depends(get_db)):
    from app.knowledge import sources

    try:
        sources.set_keys(body.values)
    except sources.SourceError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    audit.record(db, "patents.source_keys", user=user, resource_type="settings", resource_id="knowledge-sources",
                 request=request, metadata={"names": sorted(body.values)})   # 値は残さない
    return {"sources": sources.listing()}


@router.post("/sources/{source}/fetch", status_code=201)
async def fetch_source(source: str, body: FetchBody, request: Request, user: User = Depends(EDIT), db=Depends(get_db)):
    """公開データを 1 回取得して取り込む（Jobs）。検索語は外部へ送られるので、公開してよい語だけ。"""
    from app.jobs import service as jobs
    from app.knowledge import sources

    _need_public(body.approved_public)
    if source not in sources.SOURCES:
        raise HTTPException(status_code=404, detail="知らない取得先です")

    async def run(job: jobs.Job) -> dict:
        job.set_progress("取得しています", 0, 2)
        records = await asyncio.to_thread(sources.fetch, source, body.query, body.limit)
        job.set_progress("取り込んでいます", 1, 2)
        return await sources.ingest(records)

    job = jobs.create("patents.fetch", f"公開データの取得: {sources.SOURCES[source]['label']}", run, owner_user_id=user.id)
    audit.record(db, "patents.source_fetch", user=user, resource_type="source", resource_id=source, request=request,
                 metadata={"job_id": job.id, "query": body.query, "approved_public": True})
    return {"job_id": job.id}


@router.get("/cases/{case_id}/watches")
def list_watches(case_id: int, user: User = VIEW):
    from app.patents import watch

    return {"watches": _call(watch.list_watches, case_id), "hits": _call(watch.hits, case_id)}


@router.post("/cases/{case_id}/watches", status_code=201)
def create_watch(case_id: int, body: WatchBody, request: Request, user: User = Depends(EDIT), db=Depends(get_db)):
    from app.patents import watch

    _need_public(body.approved_public)
    out = _call(watch.save_watch, case_id, source=body.source, query=body.query, interval_hours=body.interval_hours,
                approved_public=True, enabled=body.enabled)
    audit.record(db, "patents.watch_create", user=user, resource_type="patent_case", resource_id=str(case_id),
                 request=request, metadata={"source": body.source, "query": body.query})
    return out


@router.put("/cases/{case_id}/watches/{watch_id}")
def update_watch(case_id: int, watch_id: int, body: WatchBody, user: User = Depends(EDIT)):
    from app.patents import watch

    return _call(watch.save_watch, case_id, source=body.source, query=body.query, interval_hours=body.interval_hours,
                 approved_public=body.approved_public, enabled=body.enabled, watch_id=watch_id)


@router.delete("/cases/{case_id}/watches/{watch_id}")
def delete_watch(case_id: int, watch_id: int, user: User = Depends(EDIT)):
    from app.patents import watch

    _call(watch.delete_watch, case_id, watch_id)
    return {"ok": True}


@router.post("/cases/{case_id}/watches/{watch_id}/run", status_code=201)
def run_watch(case_id: int, watch_id: int, user: User = Depends(EDIT)):
    from app.jobs import service as jobs
    from app.patents import watch

    if not any(w["id"] == watch_id for w in _call(watch.list_watches, case_id)):
        raise HTTPException(status_code=404, detail="ウォッチがありません")
    job = jobs.create("patents.watch", "特許ウォッチ（いま実行）", lambda j: watch.run_watch(j, watch_id), owner_user_id=user.id)
    return {"job_id": job.id}


@router.post("/cases/{case_id}/watch-hits/{hit_id}")
def decide_hit(case_id: int, hit_id: int, body: HitBody, user: User = Depends(EDIT)):
    from app.patents import watch

    return _call(watch.decide_hit, case_id, hit_id, body.status, body.category)


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
            from app.workflows import scrape_tools as st

            # 本文だけ（メニュー・フッターを除く、見出し・表は Markdown）。取得は Web 収集エンジンの安全な取得
            text, _title, final = await st.fetch_document(body.url)
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
