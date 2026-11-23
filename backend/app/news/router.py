"""ニュースの専門ページの API（/api/v1/news）。ログインした利用者。event はその人宛てだけ。設定の変更は settings.manage。"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import User
from app.news import profile, service, sources, store
from app.security.deps import get_current_user, require_permission

router = APIRouter(prefix="/news", tags=["news"])


class ActionBody(BaseModel):
    action: str = Field(pattern="^(open|like|dislike|hide|star)$")
    on: bool = True


class MuteBody(BaseModel):
    topic: str = Field(min_length=1, max_length=60)
    muted: bool = True


def _kinds(kind: str) -> list[str] | None:
    wanted = [k for k in (kind or "").split(",") if k]
    bad = [k for k in wanted if k not in sources.KINDS]
    if bad:
        raise HTTPException(status_code=422, detail=f"知らない種類です: {bad[0]}")
    return wanted or None


def _day(value: str) -> str:
    if value:
        try:
            store.day_range(value)
        except ValueError:
            raise HTTPException(status_code=422, detail="date は YYYY-MM-DD の形です") from None
    return value


@router.get("/items")
def items(view: str = Query("recommended", pattern="^(recommended|latest|starred)$"), kind: str = "", source: str = "", topic: str = "",
          q: str = Query("", max_length=200), date: str = "", before: str = "", limit: int = Query(store.PAGE_DEFAULT, ge=1, le=store.PAGE_MAX),
          user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return store.list_items(db, user.id, view=view, kinds=_kinds(kind), source=source[:200], topic=topic[:60], q=q, day=_day(date),
                            before=before, limit=limit)


@router.get("/archive")
def archive(kind: str = "", user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return store.archive_days(db, user.id, kinds=_kinds(kind))


@router.get("/trends")
def trends(days: int = Query(14, ge=1, le=90), user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return store.trends(db, user.id, days)


@router.get("/profile")
def get_profile(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return profile.summary(db, user.id)


@router.put("/profile/mute")
def mute_topic(body: MuteBody, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """「あなたの興味」から語を外す（muted=false で戻す）。"""
    profile.set_muted(user.id, body.topic, body.muted)
    return profile.summary(db, user.id)


@router.post("/items/{item_id}/action")
def item_action(item_id: int, body: ActionBody, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    state = store.set_action(db, user.id, item_id, body.action, body.on)
    if state is None:
        raise HTTPException(status_code=404, detail="記事が見つかりません")
    return {"state": state}


@router.post("/refresh")
async def refresh(user: User = Depends(get_current_user)):
    return await service.refresh()


@router.get("/sources")
def get_sources(user: User = Depends(get_current_user)):
    from app.workflows import research_keys

    return {"config": sources.load_config(), "kinds": sources.KIND_LABELS, "reddit_key": research_keys.reddit() is not None,
            "limit": sources.MAX_PER_LIST, **service.status()}


@router.put("/sources")
def put_sources(body: dict, user: User = Depends(require_permission("settings.manage"))):
    try:
        return {"config": sources.save_config(body)}
    except sources.ConfigError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
