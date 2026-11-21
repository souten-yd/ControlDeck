import logging

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from app.audit import service as audit
from app.database import get_db
from app.features import registry
from app.integrations.opencode import provider as opencode
from app.integrations.opencode import tui_history as opencode_tui_history
from app.jobs import service as jobs
from app.models import User
from app.security.deps import require_permission

logger = logging.getLogger(__name__)
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
    auto_review: bool | None = None
    auto_advisor: str | None = Field(default=None, max_length=16)
    review_model: str | None = Field(default=None, max_length=200)
    advisor_model: str | None = Field(default=None, max_length=200)
    developer_agent: bool | None = None
    developer_model: str | None = Field(default=None, max_length=200)
    ui_verify: bool | None = None
    ui_verify_allow_external: bool | None = None
    research_tool: bool | None = None
    research_external: bool | None = None
    context7: bool | None = None
    github_cli: bool | None = None
    isolation: str | None = Field(default=None, max_length=8)


WORKFLOW_KEYS = ("superpowers", "adversarial_review", "auto_review", "auto_advisor", "review_model", "advisor_model",
                 "developer_agent", "developer_model",
                 "ui_verify", "ui_verify_allow_external", "research_tool", "research_external", "context7", "github_cli", "isolation")


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
    # 解決したパスで返す（発言に記録した project_path は解決済み。比べて会話の作業フォルダを戻す）
    return {"root": str(opencode.codedev_root().resolve()), "projects": opencode.list_projects()}


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


class WorkspaceBody(BaseModel):
    # "controldeck"（ControlDeck 本体の origin/main）か "codedev:<プロジェクト名>"
    source: str = Field(min_length=1, max_length=96)
    name: str = Field(default="", max_length=32)


def _workspace_error(exc: Exception) -> HTTPException:
    return HTTPException(status_code=422, detail=str(exc))


@router.get("/workspaces")
def list_workspaces(user: User = Depends(require_permission("terminal.use"))):
    """自分の開発の作業場（開発用の箱）の一覧。他人の作業場は出さない。"""
    from app.maintenance import workspaces

    try:
        return {"workspaces": workspaces.list_for(user.username)}
    except workspaces.SandboxError as exc:
        raise _workspace_error(exc) from exc


@router.post("/workspaces", status_code=201)
def create_workspace(
    body: WorkspaceBody, request: Request,
    user: User = Depends(require_permission("terminal.use")), db=Depends(get_db),
):
    """作業場を作る（ControlDeck 本体の origin/main、または CodeDEV の git のプロジェクトから）。"""
    from app.maintenance import workspaces

    try:
        result = workspaces.create(user.username, body.source.strip(), body.name)
    except workspaces.SandboxError as exc:
        raise _workspace_error(exc) from exc
    audit.record(db, "opencode.workspace.create", user=user, resource_type="opencode_workspace",
                 resource_id=result["name"], request=request, metadata={"source": result["source"]})
    return result


@router.delete("/workspaces/{name}")
def delete_workspace(
    name: str, request: Request, confirm: bool = False,
    user: User = Depends(require_permission("terminal.use")), db=Depends(get_db),
):
    """作業場を消す。未コミット・未 push の変更があれば、confirm=true が無い限り 409 で断る。"""
    from app.integrations.opencode import chat_server
    from app.maintenance import workspaces

    try:
        box = workspaces.get(name, user.username)
        workspaces.delete(name, user.username, confirm=confirm)
    except workspaces.UnsavedChanges as exc:
        raise HTTPException(status_code=409, detail={
            "message": str(exc), "dirty": exc.dirty, "unpushed": exc.unpushed}) from exc
    except workspaces.SandboxError as exc:
        raise _workspace_error(exc) from exc
    # 会話サーバー（この作業場の中で動いていたもの）も止める。箱の pod は destroy が消している
    chat_server.stop(chat_server.server_key(box.repo, user.id))
    audit.record(db, "opencode.workspace.delete", user=user, resource_type="opencode_workspace",
                 resource_id=name, request=request, metadata={"confirmed": confirm})
    return {"ok": True, "name": name}


class AdoptBody(BaseModel):
    confirm: bool = False
    commit_message: str = Field(default="", max_length=200)
    title: str = Field(default="", max_length=120)


def _owned_box(name: str, user: User):
    from app.maintenance import workspaces

    try:
        return workspaces.get(name, user.username)
    except workspaces.SandboxError as exc:
        raise _workspace_error(exc) from exc


@router.get("/workspaces/{name}/changes")
async def workspace_changes(name: str, user: User = Depends(require_permission("terminal.use"))):
    """作業場の、元からの差分の要約（ファイル・増減・未コミット・コミット・関門の結果）。中身は返さない。"""
    import asyncio

    from app.maintenance import workspaces

    box = _owned_box(name, user)
    try:
        return await asyncio.to_thread(workspaces.changes, box)
    except workspaces.SandboxError as exc:
        raise _workspace_error(exc) from exc


def _check_payload(box) -> dict:
    from app.maintenance import workspaces

    job = jobs.get(str(box.meta.get("check_job") or ""))
    running = bool(job and job.status in {"queued", "running"})
    return {"check": workspaces.check_state(box), "running": running,
            "job_id": job.id if running and job else None}


@router.get("/workspaces/{name}/check")
async def workspace_check_state(name: str, user: User = Depends(require_permission("terminal.use"))):
    import asyncio

    from app.maintenance import sandbox

    box = _owned_box(name, user)
    box = await asyncio.to_thread(sandbox.load, name)
    return await asyncio.to_thread(_check_payload, box)


@router.post("/workspaces/{name}/check", status_code=202)
async def workspace_check(
    name: str, request: Request,
    user: User = Depends(require_permission("terminal.use")), db=Depends(get_db),
):
    """関門（dev-check）を作業場へ流す。重いので Job にする。結果は作業場の meta に保存し、GET で読む。"""
    import asyncio

    from app.maintenance import workspaces

    box = _owned_box(name, user)
    current = _check_payload(box)
    if current["running"]:
        return {"job_id": current["job_id"], "already_running": True}

    async def worker(job):
        job.log("関門を流しています")
        saved = await asyncio.to_thread(workspaces.run_check, name)
        return {"passed": saved["passed"]}

    job = jobs.create("opencode.workspace.check", f"作業場 {name} の関門", worker, owner_user_id=user.id)
    workspaces.mark_check_started(name, job.id)
    audit.record(db, "opencode.workspace.check", user=user, resource_type="opencode_workspace",
                 resource_id=name, request=request, metadata={"job_id": job.id})
    return {"job_id": job.id, "already_running": False}


@router.post("/workspaces/{name}/adopt")
async def workspace_adopt(
    name: str, body: AdoptBody, request: Request,
    user: User = Depends(require_permission("terminal.use")), db=Depends(get_db),
):
    """作業場の結果を取り込む（確認の印が必須）。本体は GitHub に PR を作る（マージはしない・settings.manage が要る）。
    CodeDEV のプロジェクトは元のリポジトリへ ws/<名前> のブランチとして取り込むだけ（外へは出さない）。"""
    import asyncio

    from app.maintenance import workspaces
    from app.security.deps import user_permissions

    if not body.confirm:
        raise HTTPException(status_code=422, detail="取り込むには確認（confirm）が必要です")
    box = _owned_box(name, user)
    source = str(box.meta.get("workspace_source") or "")
    if source == workspaces.CONTROLDECK and "settings.manage" not in user_permissions(user):
        raise HTTPException(status_code=403, detail="権限がありません: settings.manage")
    try:
        result = await asyncio.to_thread(workspaces.adopt, name, user.username,
                                         commit_message=body.commit_message, title=body.title)
    except workspaces.AdoptRefused as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except workspaces.SandboxError as exc:
        raise _workspace_error(exc) from exc
    audit.record(db, "opencode.workspace.adopt", user=user, resource_type="opencode_workspace",
                 resource_id=name, request=request,
                 metadata={"branch": result["branch"], "pr_url": result["pr_url"], "kind": result["kind"]})
    return result


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
    # 続きから開く: "last"（そのプロジェクトの最後の会話）か OpenCode の会話 ID（ses_…）
    resume: str = Field(default="", max_length=80)


_llm_warmup_tasks: set = set()


@router.get("/sessions")
def list_tui_sessions(user: User = Depends(require_permission("terminal.use"))):
    """OpenCode 画面のセッション一覧: 動いているもの（接続できる）と、閉じたもの（続きから開ける）。

    以前はブラウザの localStorage に覚えた ID だけを出していたので、TUI を閉じる（OpenCode を
    終える）と一覧から消え、別の端末からも見えなかった。サーバーに記録して返す。
    """
    from app.terminals.manager import manager as terminals

    alive = {s["id"]: s for s in terminals.list_sessions()}
    return {"sessions": opencode_tui_history.listing(user.id, alive)}


def _chat_sessions(db) -> set[str]:
    """AI アシスタント（code モード）が使っている OpenCode の会話。TUI では開かない
    （同じ会話を 2 つのサーバーが同時に書くと壊れる）。"""
    import json as _json

    from sqlalchemy import select

    from app.models import ChatMessage

    ids: set[str] = set()
    rows = db.execute(select(ChatMessage.meta_json).where(ChatMessage.meta_json.like('%"opencode_session"%'))).scalars()
    for raw in rows:
        try:
            value = (_json.loads(raw or "{}") or {}).get("opencode_session")
        except (ValueError, AttributeError):
            continue
        if isinstance(value, str):
            ids.add(value)
    return ids


def _resolve_resume(resume: str, project_path: str, runtime: str, db) -> str:
    """「続きから開く」を、そのプロジェクトで TUI が使っていた最新の会話 ID にする。

    --continue は「そのプロジェクトで最後の会話」なので、AI アシスタントの会話を掴むことがある。
    AI アシスタントの会話を除いた最新を --session で開く。無ければ新しい会話で始める。
    """
    from app.files import service as files

    chat = _chat_sessions(db)
    if resume != "last":
        if not opencode.RESUME_SESSION_RE.fullmatch(resume):
            raise HTTPException(status_code=422, detail="続きから開く会話の ID が正しくありません")
        if resume in chat:
            raise HTTPException(status_code=409, detail="AI アシスタントの会話です。AI アシスタントで続けてください")
        return resume
    try:
        project = str(files.resolve(project_path))
    except Exception:  # noqa: BLE001
        return ""
    for row in opencode.project_sessions(project, runtime):
        if row["id"] not in chat:
            return row["id"]
    return ""


class ForgetBody(BaseModel):
    project_path: str = Field(min_length=1, max_length=4096)


@router.post("/sessions/forget", status_code=204)
def forget_tui_sessions(body: ForgetBody, user: User = Depends(require_permission("terminal.use"))):
    """閉じたセッションを一覧から外す（OpenCode の会話そのものは消さない）。"""
    from app.terminals.manager import manager as terminals

    alive = {s["id"] for s in terminals.list_sessions()}
    opencode_tui_history.forget_project(body.project_path, user.id, alive)


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
        resume = body.resume.strip()
        if resume:
            resume = await asyncio.to_thread(_resolve_resume, resume, project_path, body.runtime, db)
        command, project = opencode.tui_command(
            project_path=project_path, prompt=body.prompt,
            base_url=body.base_url, model=body.model, owner_user_id=user.id,
            runtime=body.runtime, resume=resume,
        )
        session = terminals.create_session(cwd=project, command=command)
        try:
            opencode_tui_history.record(session["id"], project, user.id, resume=resume)
        except OSError as exc:  # 記録できなくても TUI は使える（一覧に出ないだけ）
            logger.warning("OpenCode TUI の記録に失敗しました: %s", exc)
    except opencode.CodeAgentError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    audit.record(db, "feature.opencode.session", user=user, resource_type="feature",
                 resource_id="opencode", request=request,
                 metadata={"terminal_id": session["id"], "project_path": project,
                           "with_prompt": bool(body.prompt.strip()), "resume": body.resume.strip()[:80],
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
        # 役割に選べる外部モデル（OpenCode からの外部送信を許可したときだけ）。
        **opencode.cloud_choices(),
        # 拡張ごとのプラグインが導入済みか。導入・更新・削除は 設定 → オプション機能。
        "installed": opencode.workflow_installed(),
        "features": {**opencode.WORKFLOW_FEATURES, **opencode.WORKFLOW_MCP_FEATURES},
        # UI 検証はこの PC の Chrome を使う。無ければ画面で案内する。
        "browser": bool(opencode._browser_executable()),
        # レビューで構文検索（ast-grep）が使えるか。
        "review_tool": opencode.review_tool_bin() is not None,
        # この PC の gh が導入・ログイン済みか（token は読まない）。
        "github": opencode.github_cli_status(),
        # いま実際に使われる場所（設定が box でも podman が無ければ host）。
        "isolation_effective": opencode.isolation_mode()}


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
