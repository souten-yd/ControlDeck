"""Web Push の購読（端末ごと）。

- GET    /push/public-key     ブラウザの pushManager.subscribe に渡す公開鍵
- GET    /push/subscriptions  自分の端末の一覧（endpoint は出さない）
- POST   /push/subscriptions  この端末を登録（同じ endpoint は上書き）
- DELETE /push/subscriptions  この端末を外す（endpoint で指す）
- POST   /push/test           自分の端末すべてへ試しに送る
- POST   /push/receipts       端末の Service Worker が「受け取った」と返す（届いたかを画面で確かめるため）
"""
from __future__ import annotations

import asyncio
import json
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.alerts import webpush
from app.audit import service as audit
from app.database import get_db
from app.models import PushSubscription, User
from app.security.crypto import encrypt_text
from app.security.deps import get_current_user

router = APIRouter(prefix="/push", tags=["push"])

MAX_DEVICES_PER_USER = 20


class SubscriptionKeys(BaseModel):
    p256dh: str = Field(min_length=80, max_length=120)
    auth: str = Field(min_length=16, max_length=32)


class SubscriptionBody(BaseModel):
    endpoint: str = Field(min_length=10, max_length=2048)
    keys: SubscriptionKeys
    label: str = Field(default="", max_length=128)


class ReceiptBody(BaseModel):
    rid: str = Field(min_length=8, max_length=64)
    shown: bool = True
    error: str = Field(default="", max_length=200)


class EndpointBody(BaseModel):
    endpoint: str = Field(min_length=10, max_length=2048)


def _subject(request: Request) -> str:
    """VAPID の sub。ControlDeck を開いている https の origin（Apple は mailto: か https: しか受けない）。"""
    origin = request.headers.get("origin") or f"{request.url.scheme}://{request.url.netloc}"
    parsed = urlparse(origin)
    host = parsed.netloc or request.url.netloc
    return f"https://{host}"[:320]


def _out(row: PushSubscription) -> dict:
    return {
        "id": row.id, "label": row.label,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "last_success_at": row.last_success_at.isoformat() if row.last_success_at else None,
        "last_received_at": row.last_received_at.isoformat() if row.last_received_at else None,
        "last_receipt_error": row.last_receipt_error or "",
    }


@router.get("/public-key")
def public_key(user: User = Depends(get_current_user)):
    return {"public_key": webpush.public_key()}


@router.get("/subscriptions")
def list_subscriptions(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    rows = db.execute(select(PushSubscription).where(PushSubscription.user_id == user.id)
                      .order_by(PushSubscription.id)).scalars().all()
    return {"items": [_out(row) for row in rows]}


@router.post("/subscriptions", status_code=201)
def subscribe(body: SubscriptionBody, request: Request, user: User = Depends(get_current_user),
              db: Session = Depends(get_db)):
    if not webpush.allowed_endpoint(body.endpoint):
        # 内側の URL へ POST させない（SSRF）。主要ブラウザの push service だけ受ける
        raise HTTPException(status_code=422, detail="この通知の宛先には送れません（対応していないブラウザです）")
    try:
        public = webpush.b64url_decode(body.keys.p256dh)
        auth = webpush.b64url_decode(body.keys.auth)
    except (ValueError, TypeError) as exc:
        raise HTTPException(status_code=422, detail="購読の鍵の形が正しくありません") from exc
    if len(public) != 65 or public[0] != 4 or len(auth) != 16:
        raise HTTPException(status_code=422, detail="購読の鍵の形が正しくありません")
    digest = webpush.endpoint_digest(body.endpoint)
    secret = encrypt_text(json.dumps({"endpoint": body.endpoint, "p256dh": body.keys.p256dh, "auth": body.keys.auth}))
    row = db.execute(select(PushSubscription).where(PushSubscription.endpoint_digest == digest)).scalar_one_or_none()
    if row is None:
        count = db.query(PushSubscription).filter(PushSubscription.user_id == user.id).count()
        if count >= MAX_DEVICES_PER_USER:
            raise HTTPException(status_code=409, detail=f"登録できる端末は {MAX_DEVICES_PER_USER} 台までです")
        row = PushSubscription(user_id=user.id, endpoint_digest=digest, secret_encrypted=secret,
                               subject=_subject(request), label=body.label.strip()[:128])
        db.add(row)
    else:
        # 同じ端末を別の人が使い始めたときは、その人の端末として付け替える
        row.user_id = user.id
        row.secret_encrypted = secret
        row.subject = _subject(request)
        row.label = body.label.strip()[:128] or row.label
    db.commit()
    audit.record(db, "push.subscribe", user=user, resource_type="push_subscription", resource_id=str(row.id), request=request)
    return _out(row)


@router.delete("/subscriptions")
def unsubscribe(body: EndpointBody, request: Request, user: User = Depends(get_current_user),
                db: Session = Depends(get_db)):
    digest = webpush.endpoint_digest(body.endpoint)
    row = db.execute(select(PushSubscription).where(
        PushSubscription.endpoint_digest == digest, PushSubscription.user_id == user.id,
    )).scalar_one_or_none()
    if row is not None:
        db.delete(row)
        db.commit()
        audit.record(db, "push.unsubscribe", user=user, resource_type="push_subscription", resource_id=str(row.id), request=request)
    return {"removed": row is not None}


@router.delete("/subscriptions/{subscription_id}")
def remove_device(subscription_id: int, request: Request, user: User = Depends(get_current_user),
                  db: Session = Depends(get_db)):
    row = db.get(PushSubscription, subscription_id)
    if row is None or row.user_id != user.id:
        raise HTTPException(status_code=404, detail="端末が見つかりません")
    db.delete(row)
    db.commit()
    audit.record(db, "push.unsubscribe", user=user, resource_type="push_subscription", resource_id=str(subscription_id), request=request)
    return {"removed": True}


@router.post("/receipts")
def receipt(body: ReceiptBody, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Service Worker が push を受けたときの返事。番号は送るたびに作り、1 回だけ使える。"""
    from datetime import datetime, timezone

    subscription_id = webpush.take_receipt(body.rid)
    row = db.get(PushSubscription, subscription_id) if subscription_id else None
    if row is None or row.user_id != user.id:
        return {"recorded": False}
    row.last_received_at = datetime.now(timezone.utc)
    row.last_receipt_error = "" if body.shown else (body.error.strip()[:200] or "表示できませんでした")
    db.commit()
    return {"recorded": True}


@router.post("/test")
async def test_push(request: Request, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    result = await asyncio.to_thread(
        webpush.send_to, title="ControlDeck の通知", body="この通知が届けば、この端末で受け取れます。",
        url="/settings/notifications", tag="push-test", user_ids=[user.id],
    )
    audit.record(db, "push.test", user=user, resource_type="push_subscription", request=request,
                 result="success" if result["sent"] else "failure", metadata=result)
    return result
