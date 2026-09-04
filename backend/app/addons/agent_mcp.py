from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.addon_runtime import grants as runtime_grants
from app.addons import execution, registry, tokens
from app.audit import service as audit
from app.database import get_db
from app.models import User
from app.project_lab import service as project_lab
from app.security.deps import user_permissions

router = APIRouter(prefix="/addons/agent-mcp", tags=["addons"])
MCP_TOKEN_TTL_SECONDS = 8 * 60 * 60
MCP_CLIENT_TIMEOUT_MS = 135_000
_SUBJECT = re.compile(r"^opencode:[A-Za-z0-9_-]{1,64}$")
PROJECT_OUTPUT_GRANT_TOOL = "control_deck.project_output_grant"
_OUTPUT_CAPABILITIES = {"projects.pick", "files.export"}


class AgentMcpCall(BaseModel):
    name: str = Field(min_length=1, max_length=192)
    arguments: dict[str, Any] = Field(default_factory=dict, max_length=64)


def issue_opencode_token(user_id: int, correlation_id: str, *, project_id: str | None = None) -> str:
    safe = re.sub(r"[^A-Za-z0-9_-]", "", correlation_id)[:64]
    if not safe:
        raise ValueError("OpenCode correlation IDが不正です")
    return tokens.issue(
        "control-deck",
        subject=f"opencode:{safe}",
        kind="agent-mcp",
        actor_user_id=user_id,
        project_id=project_id,
        ttl_seconds=MCP_TOKEN_TTL_SECONDS,
    )


def _bearer_user(
    authorization: str | None,
    db: Session,
) -> tuple[User, dict[str, Any]]:
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
    return user, claims


def _eligible_output_addons(permissions: set[str]) -> list[str]:
    if not {"project_lab.view", "files.edit"}.issubset(permissions):
        return []
    addon_ids = {str(item["addon_id"]) for item in execution.discover("agent_tools", permissions)}
    eligible: list[str] = []
    for addon_id in sorted(addon_ids):
        try:
            current = registry.status(addon_id)
        except registry.AddonRegistryError:
            continue
        if current.get("enabled") and _OUTPUT_CAPABILITIES.issubset(current.get("granted_capabilities", [])):
            eligible.append(addon_id)
    return eligible


def _project_output_tool(project_id: object, permissions: set[str]) -> dict[str, Any] | None:
    if not isinstance(project_id, str):
        return None
    addons = _eligible_output_addons(permissions)
    if not addons:
        return None
    return {
        "name": PROJECT_OUTPUT_GRANT_TOOL,
        "description": "現在のControl Deck project内に、選択したAdd-on用の短期output grantを作成します。",
        # Add-on由来のtoolと同じく、制約付きデコーダが展開できない長さ制約は落とす。
        # 実際の上限は _resolve_project_directory が引き続き強制する。
        "inputSchema": execution.model_facing_schema({
            "type": "object",
            "properties": {
                "addon_id": {"type": "string", "enum": addons},
                "relative_directory": {"type": "string", "minLength": 1, "maxLength": 512},
            },
            "required": ["addon_id", "relative_directory"],
            "additionalProperties": False,
        }),
    }


def _resolve_project_directory(project_id: str, relative_directory: object) -> Path:
    if not isinstance(relative_directory, str) or len(relative_directory) > 512:
        raise HTTPException(status_code=422, detail="project directoryが不正です")
    if "\\" in relative_directory:
        raise HTTPException(status_code=422, detail="project directoryが不正です")
    normalized = relative_directory
    parts = normalized.split("/")
    if (
        not normalized or normalized.startswith(("/", "~")) or normalized in {".", "./"}
        or any(part in {"", ".", ".."} for part in parts) or "\x00" in normalized
    ):
        raise HTTPException(status_code=422, detail="project directoryが不正です")
    try:
        project = project_lab.resolve_project(project_id)
        resolved = (project / normalized).resolve(strict=True)
    except (project_lab.ProjectLabError, FileNotFoundError, OSError) as exc:
        raise HTTPException(status_code=422, detail="project directoryが見つかりません") from exc
    if not resolved.is_dir() or not resolved.is_relative_to(project):
        raise HTTPException(status_code=422, detail="project外のdirectoryは指定できません")
    return resolved


@router.get("/tools")
async def list_tools(
    authorization: str | None = Header(default=None),
    db: Session = Depends(get_db),
):
    user, claims = _bearer_user(authorization, db)
    permissions = user_permissions(user)
    tools = await execution.agent_mcp_tools(permissions)
    project_tool = _project_output_tool(claims.get("project_id"), permissions)
    if project_tool is not None:
        tools.append(project_tool)
    return {"tools": tools}


@router.post("/call")
async def call_tool(
    body: AgentMcpCall,
    request: Request,
    authorization: str | None = Header(default=None),
    db: Session = Depends(get_db),
):
    user, claims = _bearer_user(authorization, db)
    permissions = user_permissions(user)
    if body.name == PROJECT_OUTPUT_GRANT_TOOL:
        project_id = claims.get("project_id")
        available = _project_output_tool(project_id, permissions)
        addon_id = body.arguments.get("addon_id")
        relative_directory = body.arguments.get("relative_directory")
        allowed = (
            available is not None
            and isinstance(addon_id, str)
            and addon_id in available["inputSchema"]["properties"]["addon_id"]["enum"]
            and set(body.arguments) == {"addon_id", "relative_directory"}
        )
        if not allowed or not isinstance(project_id, str):
            raise HTTPException(status_code=404, detail="project output grant toolが見つかりません")
        directory = _resolve_project_directory(project_id, relative_directory)
        try:
            result = runtime_grants.create(addon_id, user.id, str(directory), "export")
        except runtime_grants.GrantError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        audit.record(
            db,
            "addon.agent_mcp.project_output_grant",
            user=user,
            resource_type="addon",
            resource_id=addon_id,
            request=request,
            metadata={"project_id": project_id, "directory_depth": len(Path(str(relative_directory)).parts)},
        )
        return result
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
