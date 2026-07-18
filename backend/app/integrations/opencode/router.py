from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from app.audit import service as audit
from app.database import get_db
from app.features import registry
from app.integrations.opencode import provider as opencode
from app.jobs import service as jobs
from app.models import User
from app.security.deps import require_permission

router = APIRouter(prefix="/opencode", tags=["opencode"])


class SettingsBody(BaseModel):
    base_url: str = Field(min_length=8, max_length=2048)
    model: str = Field(min_length=1, max_length=200)
    project_path: str = Field(default="", max_length=4096)


class RunBody(SettingsBody):
    operation: str
    instruction: str = Field(min_length=1, max_length=32_000)


@router.get("/status")
def status(user: User = Depends(require_permission("workflows.run"))):
    return {"feature": registry.status("opencode"), "settings": opencode.get_settings()}


@router.put("/settings")
def settings(
    body: SettingsBody, request: Request,
    user: User = Depends(require_permission("settings.manage")), db=Depends(get_db),
):
    try:
        result = opencode.save_settings(body.model_dump())
    except (ValueError, OSError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    audit.record(db, "feature.opencode.settings", user=user, resource_type="feature",
                 resource_id="opencode", request=request,
                 metadata={"base_url": result["base_url"], "model": result["model"]})
    return result


@router.get("/projects")
def list_projects(user: User = Depends(require_permission("workflows.run"))):
    """CodeDEV（~/CodeDEV）配下の管理プロジェクト一覧。"""
    return {"root": str(opencode.codedev_root()), "projects": opencode.list_projects()}


class ProjectBody(BaseModel):
    name: str = Field(min_length=1, max_length=64)


@router.post("/projects", status_code=201)
def create_project(
    body: ProjectBody, request: Request,
    user: User = Depends(require_permission("terminal.use")), db=Depends(get_db),
):
    """CodeDEV配下にプロジェクトフォルダを作成する（既存名なら再利用）。"""
    try:
        project = opencode.ensure_project(body.name)
    except opencode.CodeAgentError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    audit.record(db, "feature.opencode.project", user=user, resource_type="feature",
                 resource_id="opencode", request=request,
                 metadata={"name": project["name"], "created": project["created"]})
    return project


class SessionBody(BaseModel):
    project_path: str = Field(default="", max_length=4096)
    project_name: str = Field(default="", max_length=64)
    prompt: str = Field(default="", max_length=32_000)
    base_url: str = Field(default="", max_length=2048)
    model: str = Field(default="", max_length=200)


_llm_warmup_tasks: set = set()


@router.post("/sessions", status_code=201)
async def create_session(
    body: SessionBody, request: Request,
    user: User = Depends(require_permission("terminal.use")), db=Depends(get_db),
):
    """opencode TUIの対話セッションをターミナル基盤（tmux永続・再接続対応）上で開始する。

    AIチャット等からも {project_path, prompt} を渡して起動できる共通入口。
    """
    import asyncio

    from app.terminals.manager import manager as terminals

    # opencodeは外部クライアントでControl Deck内部のondemand起動hookを通らないため、
    # セッション開始時に対象LLM endpoint（llama.cpp instance）を裏で起動しておく。
    # TUIを待たせないようfire-and-forget（Ollamaはリクエスト時に自動ロードされる）。
    endpoint = (body.base_url or opencode.get_settings()["base_url"]).strip().rstrip("/")
    from app.models_mgmt import llama

    warmup = asyncio.create_task(llama.ensure_ready_by_base_url(endpoint))
    _llm_warmup_tasks.add(warmup)
    warmup.add_done_callback(_llm_warmup_tasks.discard)

    try:
        # project_name指定はCodeDEV配下のフォルダ（無ければ作成）を使う
        project_path = body.project_path
        if body.project_name.strip():
            project_path = opencode.ensure_project(body.project_name)["path"]
        command, project = opencode.tui_command(
            project_path=project_path, prompt=body.prompt,
            base_url=body.base_url, model=body.model,
        )
        session = terminals.create_session(cwd=project, command=command)
    except opencode.CodeAgentError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    audit.record(db, "feature.opencode.session", user=user, resource_type="feature",
                 resource_id="opencode", request=request,
                 metadata={"terminal_id": session["id"], "project_path": project,
                           "with_prompt": bool(body.prompt.strip())})
    return {**session, "project_path": project}


@router.post("/run", status_code=202)
async def run(
    body: RunBody, request: Request,
    user: User = Depends(require_permission("workflows.edit")), db=Depends(get_db),
):
    if body.operation not in opencode.OPERATIONS:
        raise HTTPException(status_code=422, detail="未対応のoperationです")

    async def worker(job):
        return await opencode.provider.run(job, **body.model_dump())

    job = jobs.create("opencode.run", f"OpenCode {body.operation}", worker, owner_user_id=user.id)
    audit.record(db, "feature.opencode.run", user=user, resource_type="feature",
                 resource_id="opencode", request=request,
                 metadata={"operation": body.operation, "project_path": body.project_path})
    return {"job_id": job.id}
