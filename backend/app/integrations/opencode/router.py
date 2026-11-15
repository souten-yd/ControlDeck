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


class AgentTargetBody(BaseModel):
    base_url: str = Field(min_length=8, max_length=2048)
    model: str = Field(min_length=1, max_length=200)
    project_path: str = Field(default="", max_length=4096)


class SettingsBody(AgentTargetBody):
    # 使うOpenCodeの系列。未指定なら現在の設定を保つ（既存クライアントを壊さない）。
    runtime: str = Field(default="", max_length=8)


class WorkflowBody(BaseModel):
    superpowers: bool | None = None
    adversarial_review: bool | None = None
    auto_advisor: str | None = Field(default=None, max_length=16)
    review_model: str | None = Field(default=None, max_length=200)
    advisor_model: str | None = Field(default=None, max_length=200)
    ui_verify: bool | None = None
    ui_verify_allow_external: bool | None = None


WORKFLOW_KEYS = ("superpowers", "adversarial_review", "auto_advisor", "review_model", "advisor_model",
                 "ui_verify", "ui_verify_allow_external")


class RunBody(AgentTargetBody):
    # 系列はサーバー側の設定で決める。実行ごとに切り替えさせない
    # （runtime config も CLI の引数も系列ごとに違うため）。
    operation: str
    instruction: str = Field(min_length=1, max_length=32_000)


@router.get("/status")
def status(runtime: str = "", user: User = Depends(require_permission("workflows.run"))):
    """画面が必要とする状態。

    feature は OpenCode v2 の状態。v1 は撤去したので runtimes は v2 だけを持つ。
    """
    runtimes = opencode.runtime_states()
    active = opencode.active_runtime(runtime)
    return {"feature": runtimes[active], "settings": opencode.get_settings(),
            "active_runtime": active, "runtimes": runtimes}


@router.put("/settings")
def settings(
    body: SettingsBody, request: Request,
    user: User = Depends(require_permission("settings.manage")), db=Depends(get_db),
):
    patch = {key: value for key, value in body.model_dump().items() if key != "runtime" or value}
    try:
        result = opencode.save_settings(patch)
    except (ValueError, OSError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    audit.record(db, "feature.opencode.settings", user=user, resource_type="feature",
                 resource_id="opencode", request=request,
                 metadata={"base_url": result["base_url"], "model": result["model"],
                           "runtime": result["runtime"]})
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


@router.delete("/projects/{name}")
def delete_project(
    name: str, request: Request,
    user: User = Depends(require_permission("terminal.use")), db=Depends(get_db),
):
    """CodeDEV配下のプロジェクトをフォルダごと削除する（UI側で確認済みの前提）。"""
    try:
        result = opencode.delete_project(name)
    except opencode.CodeAgentError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    audit.record(db, "feature.opencode.project_delete", user=user, resource_type="feature",
                 resource_id="opencode", request=request, metadata={"name": result["name"]})
    return {"ok": True, "name": result["name"]}


class SessionBody(BaseModel):
    project_path: str = Field(default="", max_length=4096)
    project_name: str = Field(default="", max_length=64)
    prompt: str = Field(default="", max_length=32_000)
    base_url: str = Field(default="", max_length=2048)
    model: str = Field(default="", max_length=200)
    # このセッションで使う系列。v1画面とv2画面がそれぞれ自分の系列を名指しする。
    # 空なら設定値（チャットやワークフローと同じ既定）を使う。
    runtime: str = Field(default="", max_length=8)
    # 作業の種類（ui_verify / research / review）。空ならふつうのセッション。
    task: str = Field(default="", max_length=16)


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
    from app.models_mgmt import local_llm

    warmup = asyncio.create_task(local_llm.ensure_ready_by_base_url(endpoint))
    _llm_warmup_tasks.add(warmup)
    warmup.add_done_callback(_llm_warmup_tasks.discard)

    try:
        # project_name指定はCodeDEV配下のフォルダ（無ければ作成）を使う。
        # project_path指定でCodeDEV外の場合はコピーして取り込む（管理領域へ集約）。
        project_path = body.project_path
        if body.project_name.strip():
            project_path = opencode.ensure_project(body.project_name)["path"]
        elif project_path.strip():
            imported = await asyncio.to_thread(opencode.import_project, project_path)
            project_path = imported["path"]
        command, project = opencode.tui_command(
            project_path=project_path, prompt=opencode.task_prompt(body.task, body.prompt),
            base_url=body.base_url, model=body.model, owner_user_id=user.id,
            runtime=body.runtime,
        )
        session = terminals.create_session(cwd=project, command=command)
    except opencode.CodeAgentError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    audit.record(db, "feature.opencode.session", user=user, resource_type="feature",
                 resource_id="opencode", request=request,
                 metadata={"terminal_id": session["id"], "project_path": project,
                           "with_prompt": bool(body.prompt.strip()), "task": body.task,
                           "runtime": opencode.active_runtime(body.runtime)})
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


@router.get("/workflow")
def get_workflow(user: User = Depends(require_permission("workflows.run"))):
    from app.models_mgmt import local_llm

    settings = opencode.get_settings()
    return {**{key: settings[key] for key in WORKFLOW_KEYS},
        # 選べるモデル（ゲートウェイの名前）。別のモデルへは要求に合わせて載せ替える。
        "models": [{"alias": str(item["alias"]), "runtime": str(item.get("runtime") or "")}
                   for item in local_llm.llm_instances()],
        # 拡張ごとのプラグインが導入済みか。導入・更新・削除は 設定 → オプション機能。
        "installed": opencode.workflow_installed(),
        "features": {**opencode.WORKFLOW_FEATURES, **opencode.WORKFLOW_MCP_FEATURES},
        # UI 検証はこの PC の Chrome を使う。無ければ画面で案内する。
        "browser": bool(opencode._browser_executable())}


@router.put("/workflow")
def put_workflow(body: WorkflowBody, user: User = Depends(require_permission("settings.manage"))):
    try:
        settings = opencode.save_workflow(body.model_dump(exclude_none=True))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {key: settings[key] for key in WORKFLOW_KEYS}


@router.post("/tools/ui-verify/diagnose")
async def diagnose_ui_verify(user: User = Depends(require_permission("settings.manage"))):
    """UI 検証の道具（Playwright MCP）を session と同じ指定で起こし、どこまで動くかを返す。"""
    from app.integrations.opencode.diagnose import diagnose_ui_verify as run

    return await run()


# ---- 作業の結果（UI 検証・精密調査・レビュー） ----


def _report_owner(user: User) -> int | None:
    """管理者は全員分を見られる。それ以外は自分の session の結果だけ。"""
    from app.security.deps import user_permissions

    return None if "settings.manage" in user_permissions(user) else user.id


@router.get("/reports")
def list_reports(limit: int = 50, user: User = Depends(require_permission("workflows.run"))):
    from app.integrations.opencode import reports

    return {"reports": reports.list_reports(owner_user_id=_report_owner(user), limit=max(1, min(limit, 200)))}


@router.get("/reports/{report_id}")
def get_report(report_id: str, user: User = Depends(require_permission("workflows.run"))):
    from app.integrations.opencode import reports

    try:
        record = reports.get(report_id, owner_user_id=_report_owner(user))
    except reports.ReportError:
        record = None
    if record is None:
        raise HTTPException(status_code=404, detail="結果が見つかりません")
    return record


@router.get("/reports/{report_id}/images/{name}")
def get_report_image(report_id: str, name: str, user: User = Depends(require_permission("workflows.run"))):
    from fastapi.responses import FileResponse

    from app.integrations.opencode import reports

    try:
        path = reports.image_path(report_id, name, owner_user_id=_report_owner(user))
    except reports.ReportError:
        path = None
    if path is None:
        raise HTTPException(status_code=404, detail="画面写真が見つかりません")
    return FileResponse(path, headers={"Cache-Control": "private, max-age=86400"})


@router.delete("/reports/{report_id}")
def delete_report(report_id: str, request: Request,
                  user: User = Depends(require_permission("workflows.edit")), db=Depends(get_db)):
    from app.integrations.opencode import reports

    try:
        removed = reports.delete(report_id, owner_user_id=_report_owner(user))
    except reports.ReportError:
        removed = False
    if not removed:
        raise HTTPException(status_code=404, detail="結果が見つかりません")
    audit.record(db, "opencode.report.delete", user=user, resource_type="opencode_report",
                 resource_id=report_id, request=request)
    return {"deleted": True}
