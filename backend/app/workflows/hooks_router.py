"""Webhook トリガーの受け口。

trigger.config が {mode: "webhook", webhook_token: "..."} のワークフローを
POST /api/v1/hooks/{token} で外部から起動する。セッション認証は使わない
（トークン自体が秘密。16 文字以上を必須とし、総当たり対策で固定レートを制限）。
"""
from __future__ import annotations

import json
import logging
import time

from fastapi import APIRouter, HTTPException, Request
from sqlalchemy import select

from app.database import SessionLocal
from app.models import Workflow
from app.workflows import engine

logger = logging.getLogger("control_deck.hooks")

router = APIRouter(prefix="/hooks", tags=["hooks"])

# シンプルなレート制限（IP ごと 30 回/分）
_hits: dict[str, list[float]] = {}


def _rate_ok(ip: str) -> bool:
    now = time.time()
    bucket = [t for t in _hits.get(ip, []) if now - t < 60]
    bucket.append(now)
    _hits[ip] = bucket
    return len(bucket) <= 30


@router.post("/{token}")
async def fire_webhook(token: str, request: Request):
    if len(token) < 16:
        raise HTTPException(status_code=404, detail="not found")
    ip = request.client.host if request.client else ""
    if not _rate_ok(ip):
        raise HTTPException(status_code=429, detail="rate limited")

    def find() -> int | None:
        db = SessionLocal()
        try:
            rows = db.execute(select(Workflow).where(Workflow.enabled.is_(True))).scalars().all()
            for wf in rows:
                try:
                    nodes, _ = engine.parse_definition(wf.definition_json)
                except engine.DefinitionError:
                    continue
                trigger = next((n for n in nodes if n.get("type") == "trigger"), None)
                config = (trigger or {}).get("config") or {}
                if config.get("mode") == "webhook" and str(config.get("webhook_token", "")) == token:
                    return wf.id
            return None
        finally:
            db.close()

    import asyncio

    wf_id = await asyncio.to_thread(find)
    if wf_id is None:
        raise HTTPException(status_code=404, detail="not found")

    try:
        body = await request.json()
        if not isinstance(body, dict):
            body = {"payload": body}
    except Exception:
        raw = (await request.body())[:4000]
        body = {"payload": raw.decode(errors="replace")} if raw else {}
    body.setdefault("message", json.dumps(body.get("payload", body), ensure_ascii=False)[:2000] if body else "")
    execution_id = await engine.run_workflow(wf_id, trigger_type="webhook", input_data=body)
    logger.info("webhook fired workflow %s (execution %s)", wf_id, execution_id)
    return {"execution_id": execution_id}
