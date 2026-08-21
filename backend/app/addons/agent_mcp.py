from __future__ import annotations

import re
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.addons import execution, tokens
from app.audit import service as audit
from app.database import get_db
from app.models import User
from app.security.deps import user_permissions

router = APIRouter(prefix="/addons/agent-mcp", tags=["addons"])
MCP_TOKEN_TTL_SECONDS = 8 * 60 * 60
_SUBJECT = re.compile(r"^opencode:[A-Za-z0-9_-]{1,64}$")


class AgentMcpCall(BaseModel):
    name: str = Field(min_length=1, max_length=192)
    arguments: dict[str, Any] = Field(default_factory=dict, max_length=64)


def issue_opencode_token(user_id: int, correlation_id: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9_-]", "", correlation_id)[:64]
    if not safe:
        raise ValueError("OpenCode correlation IDが不正です")
    return tokens.issue(
        "control-deck",
        subject=f"opencode:{safe}",
        kind="agent-mcp",
        actor_user_id=user_id,
        ttl_seconds=MCP_TOKEN_TTL_SECONDS,
    )


def _bearer_user(
    authorization: str | None,
    db: Session,
) -> User:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="OpenCode Add-on MCP tokenが必要です")
    try:
        claims = tokens.verify(
            authorization.removeprefix("Bearer ").strip(),
            addon_id="control-deck",
            kind="agent-mcp",
            max_ttl_seconds=MCP_TOKEN_TTL_SECONDS,
        )
    except tokens.AddonTokenError as exc:
        raise HTTPException(status_code=401, detail="OpenCode Add-on MCP tokenが無効です") from exc
    subject = claims.get("sub")
    actor_user_id = claims.get("actor_user_id")
    if not isinstance(subject, str) or _SUBJECT.fullmatch(subject) is None or not isinstance(actor_user_id, int):
        raise HTTPException(status_code=401, detail="OpenCode Add-on MCP tokenが無効です")
    user = db.get(User, actor_user_id)
    if user is None or not user.is_active or "workflows.run" not in user_permissions(user):
        raise HTTPException(status_code=403, detail="Add-on agent toolを利用できません")
    return user


@router.get("/tools")
async def list_tools(
    authorization: str | None = Header(default=None),
    db: Session = Depends(get_db),
):
    user = _bearer_user(authorization, db)
    return {"tools": await execution.agent_mcp_tools(user_permissions(user))}


@router.post("/call")
async def call_tool(
    body: AgentMcpCall,
    request: Request,
    authorization: str | None = Header(default=None),
    db: Session = Depends(get_db),
):
    user = _bearer_user(authorization, db)
    permissions = user_permissions(user)
    target = await execution.agent_mcp_target(body.name, permissions)
    if target is None:
        raise HTTPException(status_code=404, detail="Add-on agent toolが見つかりません")
    addon_id, contribution_id = target
    try:
        job = await execution.create_agent_tool_job(
            addon_id,
            contribution_id,
            body.arguments,
            owner_user_id=user.id,
            permissions=permissions,
        )
        result = await execution.wait_agent_tool_job(job)
    except execution.AddonExecutionError as exc:
        audit.record(
            db,
            "addon.agent_mcp",
            user=user,
            resource_type="addon",
            resource_id=addon_id,
            result="failure",
            request=request,
            metadata={"contribution_id": contribution_id, "result_code": exc.code},
        )
        raise HTTPException(status_code=exc.status_code, detail={"code": exc.code, "message": str(exc)}) from exc
    audit.record(
        db,
        "addon.agent_mcp",
        user=user,
        resource_type="addon",
        resource_id=addon_id,
        request=request,
        metadata={"contribution_id": contribution_id, "job_id": job.id},
    )
    return result
