"""記憶の設定・一覧・編集・削除・会話ごとの切り替え（/api/v1/memory）。すべて本人の記憶だけ。"""
from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.audit import service as audit
from app.database import get_db
from app.memory import service, store
from app.models import Conversation, User
from app.security.deps import require_permission

router = APIRouter(prefix="/memory", tags=["memory"])
USE = Depends(require_permission("workflows.run"))


def _conversation(db: Session, conv_id: str, user: User) -> Conversation:
    conv = db.get(Conversation, conv_id)
    if conv is None or conv.owner_user_id != user.id:
        raise HTTPException(404, "会話が見つかりません")
    return conv


class PrefsBody(BaseModel):
    enabled: bool | None = None
    creation: str | None = Field(default=None, pattern="^(off|explicit|confirm|auto)$")
    use_external: bool | None = None
    use_opencode: bool | None = None


@router.get("/settings")
def get_settings(user: User = USE):
    return {"prefs": store.get_prefs(user.id), "kinds": store.KIND_LABELS}


@router.put("/settings")
def put_settings(body: PrefsBody, request: Request, user: User = USE, db: Session = Depends(get_db)):
    patch = body.model_dump(exclude_none=True)
    prefs = store.save_prefs(user.id, patch)
    audit.record(db, "memory.settings", user=user, resource_type="memory_settings", resource_id=str(user.id),
                 request=request, metadata={"keys": sorted(patch)})
    return {"prefs": prefs, "kinds": store.KIND_LABELS}


def _titles(db: Session, user: User, items: list[store.Memory]) -> dict[str, str | None]:
    out: dict[str, str | None] = {}
    for m in items:
        if m.source_id and m.source_id not in out:
            conv = db.get(Conversation, m.source_id)
            out[m.source_id] = conv.title if conv is not None and conv.owner_user_id == user.id else None
    return out


@router.get("/items")
def list_items(user: User = USE, db: Session = Depends(get_db)):
    items = store.list_memories(user.id)
    titles = _titles(db, user, items)
    rows = []
    for m in items:
        row = m.as_dict()
        row["conversation_title"] = titles.get(m.source_id)
        row["conversation_exists"] = m.source_id in titles and titles[m.source_id] is not None
        rows.append(row)
    return {"items": rows, "limit": store.MAX_PER_USER}


class ItemBody(BaseModel):
    text: str = Field(min_length=1, max_length=store.MAX_TEXT)
    kind: str = "fact"
    protected: bool = False


@router.post("/items", status_code=201)
def create_item(body: ItemBody, request: Request, user: User = USE, db: Session = Depends(get_db)):
    """利用者が一覧から手で足す。守る指定が無ければ normal（手で足した記憶の元は manual）。"""
    try:
        mem = store.add(user.id, body.text, kind=body.kind, sensitivity="protected" if body.protected else "normal",
                        source_kind="manual")
    except store.MemoryError_ as exc:
        raise HTTPException(409, str(exc)) from exc
    if mem is None:
        raise HTTPException(409, "同じ内容の記憶が既にあります")
    service.audit_create(user.id, mem)
    return mem.as_dict()


class ItemPatch(BaseModel):
    text: str | None = Field(default=None, min_length=1, max_length=store.MAX_TEXT)
    kind: str | None = None
    protected: bool | None = None
    pinned: bool | None = None
    approve: bool | None = None   # 候補（承認待ち）を確定する


@router.patch("/items/{memory_id}")
def patch_item(memory_id: int, body: ItemPatch, user: User = USE):
    try:
        mem = store.update(user.id, memory_id, text=body.text, kind=body.kind, protected=body.protected,
                           pinned=body.pinned, pending=False if body.approve else None)
    except store.MemoryError_ as exc:
        raise HTTPException(409, str(exc)) from exc
    if mem is None:
        raise HTTPException(404, "記憶が見つかりません")
    return mem.as_dict()


@router.delete("/items/{memory_id}", status_code=204)
def delete_item(memory_id: int, user: User = USE):
    if not store.delete(user.id, memory_id):
        raise HTTPException(404, "記憶が見つかりません")
    service.audit_delete(user.id, memory_id)


@router.delete("/items")
def delete_items(conversation_id: str = Query(default="", max_length=64), user: User = USE):
    """conversation_id を付けるとその会話から作った記憶だけ、無ければ本人の記憶をすべて消す。"""
    if conversation_id:
        count = store.delete_by_source(user.id, conversation_id)
        service.audit_delete(user.id, conversation_id, count=count, scope="conversation")
    else:
        count = store.delete_all(user.id)
        service.audit_delete(user.id, "all", count=count, scope="all")
    return {"deleted": count}


@router.get("/used")
def used_items(conv_id: str = Query(max_length=64), ids: str = Query(default="", max_length=400),
               user: User = USE, db: Session = Depends(get_db)):
    """答えの下の「記憶を n 件使いました」の中身。秘密でない会話には protected を返さない（作った時の回が秘密でも同じ）。"""
    conv = _conversation(db, conv_id, user)
    secret = conv.confidential_at is not None
    wanted = {int(i) for i in ids.split(",") if i.strip().isdigit()}
    rows = []
    for m in store.list_memories(user.id, include_pending=False):
        if m.id in wanted and (secret or not m.protected):
            rows.append({"id": m.id, "text": m.text, "kind": m.kind, "kind_label": store.KIND_LABELS.get(m.kind, m.kind),
                         "protected": m.protected})
    return {"items": rows}


class FlagsBody(BaseModel):
    no_use: bool | None = None
    no_save: bool | None = None


@router.get("/conversations/{conv_id}/flags")
def get_flags(conv_id: str, user: User = USE, db: Session = Depends(get_db)):
    _conversation(db, conv_id, user)
    flags = store.get_flags(conv_id)
    return {"no_use": flags["no_use"], "no_save": flags["no_save"]}


@router.put("/conversations/{conv_id}/flags")
def put_flags(conv_id: str, body: FlagsBody, user: User = USE, db: Session = Depends(get_db)):
    _conversation(db, conv_id, user)
    flags = store.set_flags(conv_id, user.id, no_use=body.no_use, no_save=body.no_save)
    return {"no_use": flags["no_use"], "no_save": flags["no_save"]}
