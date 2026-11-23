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
    # 開発の作業場: data のある所のディスクの空きの下限（GiB）。None（未指定）なら変えない。
    workspace_min_free_gib: float | None = Field(default=None, ge=0, le=4096)


class WorkflowBody(BaseModel):
    superpowers: bool | None = None
    adversarial_review: bool | None = None
    auto_review: bool | None = None
    auto_advisor: str | None = Field(default=None, max_length=16)
    review_model: str | None = Field(default=None, max_length=200)
    advisor_model: str | None = Field(default=None, max_length=200)
    developer_agent: bool | None = None
    developer_model: str | None = Field(default=None, max_length=200)
    # 役割ごとのモデル {plan, build, research, quick_check, final_check, evaluate, advise}（空＝既定）。roles.py
    roles: dict[str, str] | None = None
    ui_verify: bool | None = None
    ui_verify_allow_external: bool | None = None
    workspace_media_auto_delete: bool | None = None
    research_tool: bool | None = None
    research_external: bool | None = None
    context7: bool | None = None
    github_cli: bool | None = None
    isolation: str | None = Field(default=None, max_length=8)


class RolesCheckBody(BaseModel):
    roles: dict[str, str] = Field(default_factory=dict)


WORKFLOW_KEYS = ("superpowers", "adversarial_review", "auto_review", "auto_advisor", "review_model", "advisor_model",
                 "developer_agent", "developer_model", "roles",
                 "ui_verify", "ui_verify_allow_external", "workspace_media_auto_delete", "research_tool", "research_external",
                 "context7", "github_cli", "isolation")


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
    patch = {key: value for key, value in body.model_dump().items()
             if value is not None and (key != "runtime" or value)}
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
    # 開発対象（app / llm_app / controldeck）。省くと元から決める（今までどおり）
    target: str = Field(default="", max_length=24)
    # 機能の上書き（llm / tools / mirror / gpu）。省くと開発対象の既定
    capabilities: list[str] | None = Field(default=None, max_length=8)


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


def _workspace_progress(db, user_id: int | None, items: list[dict]) -> dict[str, dict]:
    """作業場ごとの進み具合（Project Lab の「作業場」のタブ用）。会話の最新の OpenCode の回答の meta から組み立てる。

    state: working（OpenCode が作業中）/ waiting（質問待ち）/ done（完了）/ stopped（止まった）。
    生成中でも会話サーバーが止まっていたら stopped と出す（サーバーの再起動後に generating が
    残るのを「作業中」と見せないため）。その作業場でまだ会話が無ければ進み具合は無い（None）。
    アーカイブした会話・他人の会話は読まない（自分の会話だけ）。
    """
    import json as _json

    from sqlalchemy import select

    from app.integrations.opencode import chat_server
    from app.maintenance import sandbox as sandbox_mod
    from app.models import ChatMessage, Conversation

    if not items or user_id is None:
        return {}
    by_repo = {str(item["repo_path"]): str(item["name"]) for item in items}
    convs = dict(db.execute(select(Conversation.id, Conversation.title).where(
        Conversation.owner_user_id == user_id, Conversation.archived_at.is_(None))).all())
    if not convs:
        return {}

    def epoch(value) -> float:
        """時刻を epoch 秒へ。DB（SQLite）からは tz 落ちの naive UTC が返るので、UTC として数える。
        画面は文字列の日付をその場の時間帯で解釈してしまう（2026-11-22 レビュー: 約 9 時間古く出た）。"""
        from datetime import timezone

        if value is None:
            return 0.0
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.timestamp()

    # 作業場の repo は <sandbox の root>/<名前>/work/repo（名前は英小文字・数字・ハイフンだけ）。like で一括して拾う
    root = str(sandbox_mod.root().resolve())
    escaped = root.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    pattern = '%"project_path": "' + escaped + '/%/work/repo"%'
    rows = db.execute(
        select(ChatMessage.conversation_id, ChatMessage.role, ChatMessage.status, ChatMessage.meta_json,
               ChatMessage.created_at)
        .where(ChatMessage.conversation_id.in_(convs), ChatMessage.meta_json.like(pattern, escape="\\"))
        .order_by(ChatMessage.created_at.desc()).limit(600)
    ).all()
    progress: dict[str, dict] = {}
    for conv_id, role, status, meta_json, created_at in rows:
        try:
            meta = _json.loads(meta_json or "{}") or {}
        except ValueError:
            continue
        name = by_repo.get(str(meta.get("project_path") or ""))
        if name is None or name in progress:
            continue          # 新しい順に走るので、その作業場の最初の 1 件が最新
        pending = [p for p in (meta.get("permissions") or [])
                   if isinstance(p, dict) and p.get("status") == "pending"]
        todos = [t for t in (meta.get("todos") or []) if isinstance(t, dict)]
        if role == "user":
            # user の行の meta には project_path が入らない（chat_persist の保存は assistant の行だけ）。
            # 将来 user 行にも付く形の変化があっても、最新の 1 件として「送った直後」扱いにしておく
            state = "working"
        elif pending:
            state = "waiting"
        elif status == "generating":
            state = "working"
        elif status in ("failed", "interrupted", "canceled"):
            state = "stopped"
        else:
            state = "done"
        progress[name] = {
            "state": state,
            "todo": ({"done": sum(1 for t in todos if t.get("status") == "completed"), "total": len(todos)}
                     if todos else None),
            "last_active_at": epoch(created_at),
            # 再起動などで止まった（#561 の interrupted）。会話の一覧の「中断」の印に使う（failed とは区別する）
            "interrupted": status == "interrupted",
            "conversation_id": conv_id,
            "title": convs.get(conv_id, ""),
        }
    if any(p["state"] in ("working", "waiting") for p in progress.values()):
        # 会話サーバーが止まっていないか、動いている（生成中・質問待ち）の作業場だけ確かめる（systemctl はその分だけ）。
        # 質問待ちのままサーバーが死んでいると、会話を開いても回答は届かないので「止まった」と出す
        servers = {s.workspace: s for s in chat_server._all() if s.workspace}
        for name, state in progress.items():
            if state["state"] not in ("working", "waiting"):
                continue
            server = servers.get(name)
            if server is None or not chat_server._unit_active(server.unit):
                state["state"] = "stopped"
    return progress


@router.get("/workspaces/progress")
def workspaces_progress(
    user: User = Depends(require_permission("terminal.use")), db=Depends(get_db),
):
    """自分の作業場の一覧と進み具合（Project Lab の「作業場」タブ用の 1 本。既存の一覧と同じ形に progress を足す）。

    一覧は list_for と同じ（AI アシスタントの選択欄は軽い既存の一覧 API をそのまま使う）。
    active は動いている作業場（作業中・質問待ち）の数（タブの印に出す）。
    """
    from app.maintenance import workspaces

    try:
        items = workspaces.list_for(user.username)
    except workspaces.SandboxError as exc:
        raise _workspace_error(exc) from exc
    rows = _workspace_progress(db, user.id, items)
    for item in items:
        item["progress"] = rows.get(item["name"])
    active = sum(1 for item in items if (item["progress"] or {}).get("state") in ("working", "waiting"))
    return {"workspaces": items, "active": active}


@router.post("/workspaces", status_code=201)
def create_workspace(
    body: WorkspaceBody, request: Request,
    user: User = Depends(require_permission("terminal.use")), db=Depends(get_db),
):
    """作業場を作る（ControlDeck 本体の origin/main、または CodeDEV の git のプロジェクトから）。"""
    from app.maintenance import workspaces

    from app.security.deps import user_permissions

    source = body.source.strip()
    try:
        target, caps, _ = workspaces.plan(source, body.target, body.capabilities)
    except workspaces.SandboxError as exc:
        raise _workspace_error(exc) from exc
    needed = workspaces.required_permission(target, caps)
    if needed not in user_permissions(user):
        raise HTTPException(status_code=403, detail=f"権限がありません: {needed}")
    try:
        result = workspaces.create(user.username, source, body.name, target=target, capabilities=caps)
    except workspaces.SandboxError as exc:
        raise _workspace_error(exc) from exc
    audit.record(db, "opencode.workspace.create", user=user, resource_type="opencode_workspace",
                 resource_id=result["name"], request=request,
                 metadata={"source": result["source"], "target": result["target"], "capabilities": result["capabilities"]})
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


# ---- 画面の記録（撮影・操作テストの動画）。一覧・配信・削除（maintenance/workspace_media.py） ----
def _media_error(exc: Exception) -> HTTPException:
    from app.maintenance import workspace_media

    return HTTPException(status_code=404 if isinstance(exc, workspace_media.MediaNotFound) else 422, detail=str(exc))


def _media_file_response(box, path: str, range_header: str | None):
    """ふつうのファイルだけを、許した content-type で返す。Range（動画のシーク）に応じる。"""
    import os

    from fastapi.responses import StreamingResponse

    from app.maintenance import workspace_media

    try:
        fd, size, ctype = workspace_media.open_file(box, path)
    except workspace_media.SandboxError as exc:
        raise _media_error(exc) from exc
    start, end, status = 0, size - 1, 200
    if range_header:
        import re

        match = re.fullmatch(r"bytes=(\d*)-(\d*)", range_header.strip())
        if not match or not (match.group(1) or match.group(2)):
            os.close(fd)
            raise HTTPException(status_code=416, detail="Range が正しくありません", headers={"Content-Range": f"bytes */{size}"})
        if match.group(1):
            start = int(match.group(1))
            end = min(int(match.group(2)), size - 1) if match.group(2) else size - 1
        else:
            start = max(0, size - int(match.group(2)))
        if start >= size or start > end:
            os.close(fd)
            raise HTTPException(status_code=416, detail="Range が範囲外です", headers={"Content-Range": f"bytes */{size}"})
        status = 206

    def chunks():
        with os.fdopen(fd, "rb") as handle:
            handle.seek(start)
            left = end - start + 1
            while left > 0:
                data = handle.read(min(1024 * 1024, left))
                if not data:
                    break
                left -= len(data)
                yield data

    headers = {"Accept-Ranges": "bytes", "Content-Length": str(end - start + 1),
               "X-Content-Type-Options": "nosniff", "Content-Security-Policy": "sandbox",
               "Cache-Control": "private, no-store"}
    if status == 206:
        headers["Content-Range"] = f"bytes {start}-{end}/{size}"
    return StreamingResponse(chunks(), status_code=status, media_type=ctype, headers=headers)


def _record_media_delete(db, user, request, resource: str, done: dict) -> None:
    audit.record(db, "workspace.media.delete", user=user, resource_type="opencode_workspace",
                 resource_id=resource, request=request, metadata={"count": done["count"], "size": done["size"]})


@router.get("/workspaces-media")
async def workspaces_media_summary(user: User = Depends(require_permission("terminal.use"))):
    """自分の全作業場の、画面の記録の合計（件数・大きさ）と作業場ごとの内訳。"""
    import asyncio

    from app.maintenance import workspace_media

    return await asyncio.to_thread(workspace_media.summary_for, user.username)


@router.delete("/workspaces-media")
async def workspaces_media_delete_all(
    request: Request, user: User = Depends(require_permission("terminal.use")), db=Depends(get_db),
):
    """自分の全作業場の画面の記録を一斉に消す。"""
    import asyncio

    from app.maintenance import workspace_media

    done = await asyncio.to_thread(workspace_media.delete_all_for, user.username)
    _record_media_delete(db, user, request, "*", done)
    return done


@router.get("/workspaces/{name}/media")
async def workspace_media_list(name: str, user: User = Depends(require_permission("terminal.use"))):
    """作業場の画面の記録（撮影・動画）。新しい順・{path, kind, size, mtime}。"""
    import asyncio

    from app.maintenance import workspace_media

    box = _owned_box(name, user)
    return await asyncio.to_thread(workspace_media.media_list, box)


@router.get("/workspaces/{name}/media/file")
async def workspace_media_file(
    name: str, path: str, request: Request, user: User = Depends(require_permission("terminal.use")),
):
    """記録 1 件の中身。一覧に出た相対パスだけ。symlink は辿らない。"""
    box = _owned_box(name, user)
    return _media_file_response(box, path, request.headers.get("range"))


@router.delete("/workspaces/{name}/media/file")
async def workspace_media_delete_one(
    name: str, path: str, request: Request,
    user: User = Depends(require_permission("terminal.use")), db=Depends(get_db),
):
    import asyncio

    from app.maintenance import workspace_media

    box = _owned_box(name, user)
    try:
        size = await asyncio.to_thread(workspace_media.delete_one, box, path)
    except workspace_media.SandboxError as exc:
        raise _media_error(exc) from exc
    done = {"count": 1, "size": size}
    _record_media_delete(db, user, request, name, done)
    return done


@router.delete("/workspaces/{name}/media")
async def workspace_media_delete_all(
    name: str, request: Request,
    user: User = Depends(require_permission("terminal.use")), db=Depends(get_db),
):
    """この作業場の画面の記録を全部消す。"""
    import asyncio

    from app.maintenance import workspace_media

    box = _owned_box(name, user)
    done = await asyncio.to_thread(workspace_media.delete_all, box)
    _record_media_delete(db, user, request, name, done)
    return done


def _auto_delete_media(box, user, db, request) -> None:
    """設定「取り込み・マージの後に記録を自動で消す」がオンなら、その作業場の記録を消す。失敗しても取り込みは止めない。"""
    if not opencode.get_settings().get("workspace_media_auto_delete"):
        return
    from app.maintenance import workspace_media

    try:
        _record_media_delete(db, user, request, box.name, workspace_media.delete_all(box))
    except Exception:  # noqa: BLE001
        logger.warning("作業場 %s の画面の記録を自動で消せませんでした", box.name)


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


def _lab_payload(box) -> dict:
    from app.maintenance import sandbox

    state = sandbox.lab_state(box)
    # preview の URL は動いている間だけ（鍵は起こすたびに変わる）
    state["preview_url"] = sandbox.lab_base(box.name, state["started_at"]) if state["running"] else None
    return state


@router.get("/workspaces/{name}/lab")
async def workspace_lab_state(name: str, user: User = Depends(require_permission("terminal.use"))):
    """作業場の中で動かしているアプリの状態（動いている・待ち受け中・ログの末尾・preview の URL）。"""
    import asyncio

    box = _owned_box(name, user)
    return await asyncio.to_thread(_lab_payload, box)


class LabRunBody(BaseModel):
    command: str = Field(min_length=1, max_length=500)


@router.post("/workspaces/{name}/lab/run")
async def workspace_lab_run(
    name: str, body: LabRunBody, request: Request,
    user: User = Depends(require_permission("terminal.use")), db=Depends(get_db),
):
    """作業場の箱の中でアプリを動かす。command の {port}・{host}・{base} は置き換える。前に動かしたものは止める。"""
    import asyncio

    from app.maintenance import sandbox

    box = _owned_box(name, user)
    try:
        await asyncio.to_thread(sandbox.lab_start, box, body.command, by=user.username)
    except sandbox.SandboxError as exc:
        raise _workspace_error(exc) from exc
    audit.record(db, "workspace.lab.run", user=user, resource_type="opencode_workspace",
                 resource_id=name, request=request, metadata={"command": body.command.strip()[:200]})
    return await asyncio.to_thread(_lab_payload, box)


@router.post("/workspaces/{name}/lab/stop")
async def workspace_lab_stop(
    name: str, request: Request, user: User = Depends(require_permission("terminal.use")), db=Depends(get_db),
):
    import asyncio

    from app.maintenance import sandbox

    box = _owned_box(name, user)
    await asyncio.to_thread(sandbox.lab_stop, box)
    audit.record(db, "workspace.lab.stop", user=user, resource_type="opencode_workspace",
                 resource_id=name, request=request)
    return await asyncio.to_thread(_lab_payload, box)


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


class CheckBody(BaseModel):
    # 画面の見た目（画像を読むモデルの目）を人が確かめたので、その項目だけ外して流す。管理者だけ・最後の関門が
    # 見た目だけで落ちたときだけ（2026-11-22: 誤検出で正しい変更が取り込めなかった）
    skip_visual: bool = False


@router.post("/workspaces/{name}/check", status_code=202)
async def workspace_check(
    name: str, request: Request, body: CheckBody | None = None,
    user: User = Depends(require_permission("terminal.use")), db=Depends(get_db),
):
    """関門（dev-check）を作業場へ流す。重いので Job にする。結果は作業場の meta に保存し、GET で読む。"""
    import asyncio

    from app.maintenance import sandbox, workspaces
    from app.security.deps import user_permissions

    box = _owned_box(name, user)
    current = _check_payload(box)
    if current["running"]:
        return {"job_id": current["job_id"], "already_running": True}
    skip_by = ""
    if body is not None and body.skip_visual:
        if "settings.manage" not in user_permissions(user):
            raise HTTPException(403, "見た目の項目を外せるのは管理者だけです")
        if not workspaces.only_visual_failed(sandbox._read_meta(box.path).get("last_check")):
            raise HTTPException(409, "見た目の項目を外せるのは、最後の関門が画面の見た目だけで落ちたときです")
        skip_by = user.username

    async def worker(job):
        job.log("関門を流しています" + ("（画面の見た目は人が確かめたので外す）" if skip_by else ""))
        saved = await asyncio.to_thread(workspaces.run_check, name, skip_visual_by=skip_by)
        from app.alerts.events import notify_event

        notify_event("workspace_gate", title=f"関門{'に合格しました' if saved['passed'] else 'で不合格でした'}",
                     body=f"作業場 {name}", url="/assistant", tag=f"gate-{name}", user_ids=[user.id])
        return {"passed": saved["passed"]}

    job = jobs.create("opencode.workspace.check", f"作業場 {name} の関門", worker, owner_user_id=user.id)
    workspaces.mark_check_started(name, job.id)
    audit.record(db, "opencode.workspace.check", user=user, resource_type="opencode_workspace",
                 resource_id=name, request=request, metadata={"job_id": job.id, "skip_visual": bool(skip_by)})
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
    if box.adopt_mode == "pr" and "settings.manage" not in user_permissions(user):
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
                 metadata={"branch": result["branch"], "pr_url": result["pr_url"], "kind": result["kind"],
                           "target": box.target, "capabilities": sorted(box.capabilities)})
    _auto_delete_media(box, user, db, request)
    from app.alerts.events import notify_event

    notify_event("workspace_adopted", title="作業場を取り込みました", body=f"作業場 {name}", url="/assistant",
                 tag=f"adopt-{name}", user_ids=[user.id])
    return result


@router.get("/workspaces/{name}/checkpoints")
async def workspace_checkpoints(name: str, user: User = Depends(require_permission("terminal.use"))):
    """戻せる保存の時点（新しい順）と、過去の「戻す前」。"""
    import asyncio

    from app.maintenance import workspaces

    box = _owned_box(name, user)
    try:
        return {"checkpoints": await asyncio.to_thread(workspaces.checkpoints, box)}
    except workspaces.SandboxError as exc:
        raise _workspace_error(exc) from exc


class RestoreBody(BaseModel):
    sha: str = Field(min_length=40, max_length=40)


def _generating_in(db, box) -> bool:
    """この作業場で OpenCode が今も回答を作っているか（戻すと作業中のファイルが変わるので止める）。"""
    from sqlalchemy import select

    from app.models import ChatMessage

    escaped = str(box.repo.resolve()).replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    rows = db.execute(select(ChatMessage.job_id).where(
        ChatMessage.status == "generating", ChatMessage.meta_json.like(f'%"project_path": "{escaped}"%', escape="\\"),
    )).scalars().all()
    return any((job := jobs.get(job_id)) is not None and job.status in ("queued", "running") for job_id in rows if job_id)


@router.post("/workspaces/{name}/restore")
async def workspace_restore(
    name: str, body: RestoreBody, request: Request,
    user: User = Depends(require_permission("terminal.use")), db=Depends(get_db),
):
    """作業場のコードを、保存の時点へ戻す（戻す前の状態も残るので、あとで戻せる）。作業中の会話があれば 409。"""
    import asyncio

    from app.maintenance import workspaces

    box = _owned_box(name, user)
    if _generating_in(db, box):
        raise HTTPException(status_code=409, detail="OpenCode が作業中です。終わるか止めてから戻してください")
    try:
        result = await asyncio.to_thread(workspaces.restore, box, body.sha, user.username)
    except workspaces.SandboxError as exc:
        raise _workspace_error(exc) from exc
    audit.record(db, "workspace.restore", user=user, resource_type="opencode_workspace",
                 resource_id=name, request=request, metadata={"to": body.sha, "before": result["before"]})
    return result


@router.get("/workspaces/{name}/diff")
async def workspace_diff(name: str, path: str = "", user: User = Depends(require_permission("terminal.use"))):
    """元からの差分。path なしはファイルの一覧、ありはそのファイルの unified diff（256KB まで）。export.git から取る。"""
    import asyncio

    from app.maintenance import workspaces

    box = _owned_box(name, user)
    try:
        return await asyncio.to_thread(workspaces.file_diff, box, path)
    except workspaces.SandboxError as exc:
        raise _workspace_error(exc) from exc


class MergeBody(BaseModel):
    confirm: bool = False
    acknowledge_l2: bool = False                      # L2 を確かめてマージのボタン（2026-11-23）
    l2_phrase: str = Field(default="", max_length=60)  # 古い確認の文言（互換）


@router.get("/workspaces/{name}/merge")
async def workspace_merge_preview(name: str, user: User = Depends(require_permission("settings.manage"))):
    """マージの前提の確認（関門・区分・マージできるか）。何も書かない。"""
    import asyncio

    from app.maintenance import workspaces

    _owned_box(name, user)
    try:
        return await asyncio.to_thread(workspaces.merge_preview, name, user.username)
    except workspaces.AdoptRefused as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except workspaces.SandboxError as exc:
        raise _workspace_error(exc) from exc


@router.post("/workspaces/{name}/merge")
async def workspace_merge(
    name: str, body: MergeBody, request: Request,
    user: User = Depends(require_permission("settings.manage")), db=Depends(get_db),
):
    """取り込んだ PR をマージする（settings.manage・確認の印が必須。L2 は確認の文言も要る）。Release はしない。"""
    import asyncio

    from app.maintenance import workspaces

    if not body.confirm:
        raise HTTPException(status_code=422, detail="マージするには確認（confirm）が必要です")
    box = _owned_box(name, user)
    try:
        result = await asyncio.to_thread(workspaces.merge, name, user.username,
                                   acknowledge_l2=body.acknowledge_l2, confirm_phrase=body.l2_phrase)
    except workspaces.AdoptRefused as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except workspaces.SandboxError as exc:
        raise _workspace_error(exc) from exc
    audit.record(db, "opencode.workspace.merge", user=user, resource_type="opencode_workspace",
                 resource_id=name, request=request,
                 metadata={"pr_url": result["pr_url"], "tier": result["tier"], "head": result["head"],
                           "guarded": result["guarded"]})
    _auto_delete_media(box, user, db, request)
    from app.alerts.events import notify_event

    notify_event("workspace_adopted", title="作業場をマージしました", body=f"作業場 {name}", url="/assistant",
                 tag=f"merge-{name}", user_ids=[user.id])
    return result


@router.post("/workspaces/{name}/resolve-conflict", status_code=202)
async def workspace_resolve_conflict(
    name: str, request: Request,
    user: User = Depends(require_permission("workflows.run")), db=Depends(get_db),
):
    """main の取り込みでぶつかったファイルを、この作業場で動いた同じ会話の OpenCode に直させる。

    元の main は merge_main が箱の中の MAIN_REF へ取り込み済み。ぶつかったファイルの一覧を user
    メッセージとして会話へ送る（通常の送信と同じ流れ・workflows.run）。セッションは会話の meta のものを続ける。"""
    import asyncio

    from app.maintenance import workspaces
    from app.models import Conversation
    from app.workflows import chat_persist

    box = _owned_box(name, user)
    conflict = await asyncio.to_thread(workspaces.merge_conflict_state, box)
    if conflict is None:
        raise HTTPException(status_code=409,
                            detail="ぶつかったファイルの記録がありません。もう一度「取り込む」を試してください")
    conv_id = await asyncio.to_thread(workspaces.last_conversation, box)
    conv = db.get(Conversation, conv_id) if conv_id else None
    if conv is None or conv.owner_user_id != user.id:
        raise HTTPException(status_code=409, detail="この作業場で OpenCode が動いた会話が見つかりません")
    files = "\n".join(f"- {f}" for f in (conflict.get("conflicts") or [])[:50])
    content = ("main を取り込もうとしたら、次のファイルがぶつかりました:\n"
               f"{files}\n\n"
               f"元の main はこの作業場の中の `{workspaces.MAIN_REF}` に取り込み済みです。"
               f"`git merge {workspaces.MAIN_REF}` でぶつかりを解決してください"
               "（main の変更とこの作業の内容を両方残す）。解決したらコミットして、残りの作業を続けてください。")
    body = chat_persist.SendBody(content=content, mode="code", code_project_path=str(box.repo))
    result = await chat_persist._start_send(db, conv, body, user.id)
    audit.record(db, "opencode.workspace.resolve_conflict", user=user, resource_type="opencode_workspace",
                 resource_id=name, request=request,
                 metadata={"conversation_id": conv_id, "files": len(conflict.get("conflicts") or [])})
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


def _role_info(settings: dict, roles: dict | None = None, *, strict: bool = False) -> dict:
    """画面向け: 選んだ役割と、実際に使われるモデル（既定を解決したもの）・注意。strict=True は同じ段のローカルが 2 つなら error を返す。"""
    from app.integrations.opencode import roles as roles_mod

    candidate = {**settings, "roles": roles if roles is not None else settings.get("roles") or {}}
    if roles is not None:
        candidate["review_model"] = ""   # 画面が送ってきた roles が最終検証まで含めて正
    try:
        resolved = opencode.role_resolution(candidate, str(settings.get("model") or ""), strict=strict)
    except roles_mod.RolesError as exc:
        return {"error": str(exc), "effective": {}, "notes": []}
    return {"error": "", "effective": resolved.view(), "notes": resolved.notes}


def _roles_for_screen(settings: dict) -> dict:
    """保存済みの roles。旧 review_model だけで動いていた設定は、最終検証として見せる（移行）。"""
    roles = dict(settings.get("roles") or {})
    if settings.get("review_model") and not roles.get("final_check"):
        roles["final_check"] = settings["review_model"]
    return roles


@router.post("/roles/check")
def check_roles(body: RolesCheckBody, user: User = Depends(require_permission("workflows.run"))):
    """保存せずに、役割の選び方が決まりに合うかを確かめる（同じ段のローカルは 1 つ・最終検証＝開発の注意）。"""
    return _role_info(opencode.get_settings(), body.roles, strict=True)


@router.get("/workflow")
def get_workflow(user: User = Depends(require_permission("workflows.run"))):
    from app.models_mgmt import local_llm

    settings = opencode.get_settings()
    return {**{key: settings[key] for key in WORKFLOW_KEYS},
        "roles": _roles_for_screen(settings),
        "role_info": _role_info(settings, _roles_for_screen(settings)),
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
    return {**{key: settings[key] for key in WORKFLOW_KEYS}, "roles": _roles_for_screen(settings),
            "role_info": _role_info(settings, _roles_for_screen(settings))}


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
