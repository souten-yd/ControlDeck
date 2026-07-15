from __future__ import annotations

import uuid

from app.integrations.opencode.provider import CodeAgentError, provider
from app.jobs.service import Job
from app.workflows.nodes import NodeError, render_template


async def node_code_agent(config: dict, ctx: dict) -> dict:
    operation = str(config.get("operation") or "analyze")
    project_path = render_template(str(config.get("project_path") or ""), ctx)
    instruction = render_template(str(config.get("instruction") or ""), ctx)
    job = Job(id=f"wf-{uuid.uuid4().hex[:20]}", kind="opencode.node", title="OpenCode node", status="running")
    try:
        return await provider.run(
            job, operation=operation, project_path=project_path, instruction=instruction,
            base_url=str(config.get("base_url") or ""), model=str(config.get("model") or ""),
        )
    except CodeAgentError as exc:
        raise NodeError(str(exc)) from exc
