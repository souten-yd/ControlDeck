"""ナレッジ（RAG）管理 API。コレクション/ドキュメントの CRUD と検索テスト。"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from app.audit import service as audit
from app.database import get_db
from app.models import User
from app.security.deps import require_permission
from app.workflows import rag

router = APIRouter(prefix="/knowledge", tags=["knowledge"])


class CollectionCreate(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    config: dict = {}


class ConfigPatch(BaseModel):
    config: dict


class DocumentAdd(BaseModel):
    source: str = Field(default="", max_length=256)
    text: str = ""
    url: str = ""
    path: str = ""  # 許可ルート配下のファイル
    api_key: str = ""
    reset: bool = False


class SearchBody(BaseModel):
    question: str = Field(min_length=1, max_length=2000)
    top_k: int = Field(default=5, ge=1, le=20)
    mode: str | None = None
    api_key: str = ""


@router.get("/collections")
def list_collections(user: User = Depends(require_permission("workflows.run"))):
    return rag.list_collections()


@router.get("/defaults")
def defaults(user: User = Depends(require_permission("workflows.run"))):
    return {"config": rag.DEFAULT_CONFIG, "strategies": _strategies(), "search_modes": ["vector", "fulltext", "hybrid"]}


def _strategies() -> list[dict]:
    from app.workflows import chunkers

    labels = {
        "recursive": "再帰分割（既定・汎用）",
        "fixed": "固定長",
        "sentence": "文単位",
        "paragraph": "段落単位",
        "markdown": "Markdown 見出し単位",
        "parent_child": "親子（子で検索し親を文脈に）",
    }
    return [{"value": s, "label": labels.get(s, s)} for s in chunkers.STRATEGIES]


@router.post("/collections", status_code=201)
def create_collection(
    body: CollectionCreate,
    request: Request,
    user: User = Depends(require_permission("workflows.edit")),
    db=Depends(get_db),
):
    try:
        out = rag.create_collection(body.name, body.config)
    except rag.RagError as e:
        raise HTTPException(status_code=422, detail=str(e))
    audit.record(db, "knowledge.create", user=user, resource_type="knowledge", resource_id=body.name, request=request)
    return out


@router.get("/collections/{name}")
def get_collection(name: str, user: User = Depends(require_permission("workflows.run"))):
    if not rag.collection_exists(name):
        raise HTTPException(status_code=404, detail="コレクションが見つかりません")
    return {"collection": name, "config": rag.get_config(name), "documents": rag.list_documents(name)}


@router.patch("/collections/{name}")
def patch_collection(
    name: str,
    body: ConfigPatch,
    user: User = Depends(require_permission("workflows.edit")),
):
    if not rag.collection_exists(name):
        raise HTTPException(status_code=404, detail="コレクションが見つかりません")
    return {"collection": name, "config": rag.set_config(name, body.config)}


@router.delete("/collections/{name}")
def delete_collection(
    name: str,
    request: Request,
    user: User = Depends(require_permission("workflows.edit")),
    db=Depends(get_db),
):
    try:
        rag.delete_collection(name)
    except rag.RagError as e:
        raise HTTPException(status_code=422, detail=str(e))
    audit.record(db, "knowledge.delete", user=user, resource_type="knowledge", resource_id=name, request=request)
    return {"ok": True}


@router.post("/collections/{name}/documents", status_code=201)
async def add_document(
    name: str,
    body: DocumentAdd,
    request: Request,
    user: User = Depends(require_permission("workflows.edit")),
    db=Depends(get_db),
):
    if not rag.collection_exists(name):
        raise HTTPException(status_code=404, detail="コレクションが見つかりません")
    text, source = body.text, body.source
    # URL / ファイルからの取り込み
    if body.url:
        from app.workflows import scrape_tools as st

        try:
            html, _, final = await st.fetch(body.url)
        except st.ScrapeError as e:
            raise HTTPException(status_code=422, detail=str(e))
        from bs4 import BeautifulSoup

        text = BeautifulSoup(html, "html.parser").get_text("\n", strip=True)
        source = source or final
    elif body.path:
        from app.files.service import FileAccessError, read_text

        try:
            text = read_text(body.path)
        except (FileAccessError, FileNotFoundError, OSError) as e:
            raise HTTPException(status_code=422, detail=f"ファイル読み込み失敗: {e}")
        source = source or body.path
    if not text.strip():
        raise HTTPException(status_code=422, detail="取り込むテキストがありません")
    try:
        out = await rag.add_document(name, text, source or "document", api_key=body.api_key, reset=body.reset)
    except rag.RagError as e:
        raise HTTPException(status_code=422, detail=str(e))
    audit.record(db, "knowledge.add_doc", user=user, resource_type="knowledge", resource_id=name, request=request, metadata={"source": source})
    return out


@router.post("/collections/{name}/ingest-jobs", status_code=201)
async def ingest_job(
    name: str,
    body: DocumentAdd,
    request: Request,
    user: User = Depends(require_permission("workflows.edit")),
    db=Depends(get_db),
):
    """取り込みをサーバー側ジョブで実行する（URL取得・埋め込み中にブラウザを閉じても継続）。"""
    from app.jobs import service as jobs

    if not rag.collection_exists(name):
        raise HTTPException(status_code=404, detail="コレクションが見つかりません")
    if not (body.text.strip() or body.url.strip() or body.path.strip()):
        raise HTTPException(status_code=422, detail="取り込むテキスト・URL・ファイルのいずれかを指定してください")

    async def run(job: jobs.Job) -> dict:
        text, source = body.text, body.source
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

            text = read_text(body.path)
            source = source or body.path
        if not text.strip():
            raise rag.RagError("取り込むテキストがありません")
        job.set_progress("チャンク分割・埋め込み中", 1, 3)
        out = await rag.add_document(name, text, source or "document", api_key=body.api_key, reset=body.reset)
        job.set_progress("完了", 3, 3)
        return out

    job = jobs.create("rag.ingest", f"RAG取り込み: {name}", run, owner_user_id=user.id,
                      idempotency_key=request.headers.get("idempotency-key"))
    audit.record(db, "knowledge.add_doc", user=user, resource_type="knowledge",
                 resource_id=name, request=request,
                 metadata={"job_id": job.id, "source": body.source or body.url or body.path})
    return {"job_id": job.id}


@router.delete("/collections/{name}/documents/{doc_id}")
def delete_document(
    name: str,
    doc_id: int,
    user: User = Depends(require_permission("workflows.edit")),
):
    if not rag.collection_exists(name):
        raise HTTPException(status_code=404, detail="コレクションが見つかりません")
    rag.delete_document(name, doc_id)
    return {"ok": True}


@router.post("/collections/{name}/search")
async def search(
    name: str,
    body: SearchBody,
    user: User = Depends(require_permission("workflows.run")),
):
    try:
        return await rag.search(name, body.question, body.top_k, api_key=body.api_key, mode_override=body.mode)
    except rag.RagError as e:
        raise HTTPException(status_code=422, detail=str(e))


class GraphBuildBody(BaseModel):
    base_url: str = "http://127.0.0.1:11434/v1"
    model: str = "llama3.2"
    api_key: str = ""
    max_chunks: int = Field(default=200, ge=1, le=1000)


@router.get("/collections/{name}/graph")
def graph_stats(name: str, user: User = Depends(require_permission("workflows.run"))):
    if not rag.collection_exists(name):
        raise HTTPException(status_code=404, detail="コレクションが見つかりません")
    from app.workflows import rag_graph

    return rag_graph.graph_stats(name)


@router.post("/collections/{name}/graph")
async def build_graph(
    name: str,
    body: GraphBuildBody,
    request: Request,
    user: User = Depends(require_permission("workflows.edit")),
    db=Depends(get_db),
):
    if not rag.collection_exists(name):
        raise HTTPException(status_code=404, detail="コレクションが見つかりません")
    from app.workflows import rag_graph

    try:
        out = await rag_graph.build_graph(name, body.base_url, body.model, body.api_key, body.max_chunks)
    except rag.RagError as e:
        raise HTTPException(status_code=422, detail=str(e))
    audit.record(db, "knowledge.graph", user=user, resource_type="knowledge", resource_id=name, request=request)
    return out


@router.post("/collections/{name}/graph-jobs", status_code=201)
async def build_graph_job(
    name: str,
    body: GraphBuildBody,
    request: Request,
    user: User = Depends(require_permission("workflows.edit")),
    db=Depends(get_db),
):
    """グラフ構築をサーバー側ジョブで実行する（LLM抽出中にブラウザを閉じても継続）。"""
    from app.jobs import service as jobs

    if not rag.collection_exists(name):
        raise HTTPException(status_code=404, detail="コレクションが見つかりません")
    from app.workflows import rag_graph

    async def run(job: jobs.Job) -> dict:
        return await rag_graph.build_graph(
            name, body.base_url, body.model, body.api_key, body.max_chunks,
            on_progress=lambda done, total: job.set_progress("グラフ抽出中", done, total),
        )

    job = jobs.create("rag.graph", f"グラフ構築: {name}", run, owner_user_id=user.id,
                      idempotency_key=request.headers.get("idempotency-key"))
    audit.record(db, "knowledge.graph", user=user, resource_type="knowledge",
                 resource_id=name, request=request, metadata={"job_id": job.id})
    return {"job_id": job.id}
