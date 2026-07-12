from __future__ import annotations

import json

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import AuditLog, User
from app.security.deps import require_permission

router = APIRouter(prefix="/audit", tags=["audit"])


@router.get("")
def list_audit_logs(
    limit: int = Query(default=100, le=500),
    offset: int = 0,
    action: str | None = None,
    user: User = Depends(require_permission("audit.view")),
    db: Session = Depends(get_db),
):
    stmt = select(AuditLog).order_by(AuditLog.timestamp.desc()).limit(limit).offset(offset)
    if action:
        stmt = stmt.where(AuditLog.action == action)
    rows = db.execute(stmt).scalars().all()
    return [
        {
            "id": r.id,
            "timestamp": r.timestamp,
            "username": r.username,
            "action": r.action,
            "resource_type": r.resource_type,
            "resource_id": r.resource_id,
            "result": r.result,
            "ip_address": r.ip_address,
            "metadata": json.loads(r.metadata_json or "{}"),
        }
        for r in rows
    ]
