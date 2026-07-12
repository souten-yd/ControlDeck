"""監査ログ記録。秘密値を metadata に含めないこと。"""
from __future__ import annotations

import json
import logging

from fastapi import Request
from sqlalchemy.orm import Session

from app.models import AuditLog, User

logger = logging.getLogger("control_deck.audit")


def record(
    db: Session,
    action: str,
    *,
    user: User | None = None,
    username: str = "",
    resource_type: str = "",
    resource_id: str = "",
    result: str = "success",
    request: Request | None = None,
    metadata: dict | None = None,
) -> None:
    ip = ""
    ua = ""
    if request is not None:
        ip = request.client.host if request.client else ""
        ua = request.headers.get("user-agent", "")[:256]
    entry = AuditLog(
        user_id=user.id if user else None,
        username=user.username if user else username,
        action=action,
        resource_type=resource_type,
        resource_id=str(resource_id),
        result=result,
        ip_address=ip,
        user_agent=ua,
        metadata_json=json.dumps(metadata or {}, ensure_ascii=False),
    )
    db.add(entry)
    db.commit()
    logger.info("audit action=%s user=%s result=%s resource=%s/%s", action, entry.username, result, resource_type, resource_id)
