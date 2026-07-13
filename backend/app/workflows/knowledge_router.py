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
