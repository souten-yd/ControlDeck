from __future__ import annotations

import json
import subprocess

from fastapi import APIRouter, Depends, HTTPException, Request, WebSocket
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.applications import service as apps
from app.applications import systemd as sd
from app.applications import testrun
from app.applications.discovery import discover_project, discover_pythons
from app.audit import service as audit
from app.database import SessionLocal, get_db
from app.models import ManagedApplication, User
from app.schemas.apps import AppCreate, AppOut, AppUpdate
from app.security.deps import authenticate_websocket, require_permission

router = APIRouter(prefix="/apps", tags=["apps"])


def _get_app(db: Session, app_id: int) -> ManagedApplication:
    app = db.get(ManagedApplication, app_id)
    if app is None:
        raise HTTPException(status_code=404, detail="アプリが見つかりません")
    return app


@router.get("")
def list_apps(
    user: User = Depends(require_permission("apps.view")), db: Session = Depends(get_db)
) -> list[AppOut]:
    rows = db.execute(select(ManagedApplication).order_by(ManagedApplication.name)).scalars().all()
    return [apps.to_out(a) for a in rows]


@router.post("", status_code=201)
def create_app(
    body: AppCreate,
    request: Request,
    user: User = Depends(require_permission("apps.edit")),
    db: Session = Depends(get_db),
) -> AppOut:
    try:
        apps.validate_fields(body)
    except apps.AppValidationError as e:
        raise HTTPException(status_code=422, detail=str(e))
    app = ManagedApplication(
        name=body.name,
        description=body.description,
        application_type=body.application_type,
        working_directory=body.working_directory,
        executable_path=body.executable_path,
        script_path=body.script_path,
        python_path=body.python_path,
        url=body.url,
        web_port=body.web_port,
        arguments_json=json.dumps(body.arguments),
        auto_start=body.auto_start,
        restart_policy=body.restart_policy,
        stop_timeout_seconds=body.stop_timeout_seconds,
    )
    apps.set_environment(app, body.environment)
    db.add(app)
    db.flush()
    # インラインコードが指定されていれば保存し script_path を設定
    if body.code is not None and body.application_type in ("python_script", "shell_script"):
        apps.write_app_code(app, body.code)
    if body.application_type == "systemd_service":
        app.systemd_unit_name = body.systemd_unit_name or ""
    elif body.application_type == "url_shortcut":
        app.systemd_unit_name = ""
    else:
        app.systemd_unit_name = sd.unit_name_for(app.id)
    db.commit()
    try:
        apps.sync_unit(app)
    except (ValueError, OSError) as e:
        db.delete(app)
        db.commit()
        raise HTTPException(status_code=422, detail=f"ユニット生成に失敗しました: {e}")
    audit.record(db, "app.create", user=user, resource_type="app", resource_id=str(app.id), request=request, metadata={"name": app.name})
    return apps.to_out(app)


@router.get("/python-interpreters")
def python_interpreters(user: User = Depends(require_permission("apps.edit"))):
    return discover_pythons()


@router.get("/discover-project")
def project_discovery(
    path: str, user: User = Depends(require_permission("apps.edit"))
):
    return discover_project(path)


class TestRunBody(BaseModel):
    application_type: str
    python_path: str | None = None
    code: str
    working_directory: str | None = None


@router.post("/test-run")
async def test_run(
    body: TestRunBody,
    user: User = Depends(require_permission("apps.edit")),
):
    """インラインコードを一時的に実行して動作確認する（stdout/stderr/終了コードを返す）。

    apps.edit 権限が必要。30 秒でタイムアウト、出力は上限つき。shell=False。
    継続実行するアプリの確認にはストリーミング版（WS /apps/test-run/stream）を使う。
    """
    import asyncio
    from pathlib import Path as _Path

    try:
        argv, tmp, cwd = testrun.prepare(
            body.application_type, body.python_path, body.code, body.working_directory
        )
    except testrun.TestRunError as e:
        raise HTTPException(status_code=422, detail=str(e))

    def run() -> dict:
        try:
            r = subprocess.run(argv, capture_output=True, text=True, timeout=30, cwd=cwd)
            return {"exit_code": r.returncode, "stdout": r.stdout[-16000:], "stderr": r.stderr[-8000:], "ok": r.returncode == 0}
        finally:
            _Path(tmp).unlink(missing_ok=True)

    try:
        return await asyncio.to_thread(run)
    except subprocess.TimeoutExpired:
        raise HTTPException(status_code=408, detail="実行がタイムアウトしました（30 秒）")
    except OSError as e:
        raise HTTPException(status_code=500, detail=f"実行に失敗しました: {e}")


@router.websocket("/test-run/stream")
async def test_run_stream(websocket: WebSocket):
    """インラインコードをストリーミング実行する。

    最初のメッセージ: {application_type, python_path?, code, working_directory?}
    サーバー → {type: start|stdout|stderr|notice|exit|error, ...}
    クライアント → {type: "stop"} で停止。切断時もプロセスを終了する。
    """
    import asyncio
    import json as _json

    db = SessionLocal()
    try:
        user = await authenticate_websocket(websocket, db, "apps.edit")
        if user is None:
            return
    finally:
        db.close()
    await websocket.accept()
    try:
        first = await asyncio.wait_for(websocket.receive_text(), timeout=15)
        body = _json.loads(first)
        argv, tmp, cwd = testrun.prepare(
            body.get("application_type", ""),
            body.get("python_path") or None,
            body.get("code", ""),
            body.get("working_directory") or None,
        )
    except testrun.TestRunError as e:
        await websocket.send_text(_json.dumps({"type": "error", "message": str(e)}, ensure_ascii=False))
        await websocket.close()
        return
    except (asyncio.TimeoutError, _json.JSONDecodeError):
        await websocket.close(code=4400)
        return
    await testrun.stream_run(websocket, argv, tmp, cwd)
    try:
        await websocket.close()
    except RuntimeError:
        pass


@router.get("/{app_id}/code")
def get_app_code(
    app_id: int,
    user: User = Depends(require_permission("apps.edit")),
    db: Session = Depends(get_db),
):
    app = _get_app(db, app_id)
    return {"code": apps.read_app_code(app), "managed": apps.is_managed_code(app)}


@router.get("/{app_id}")
def get_app(
    app_id: int,
    user: User = Depends(require_permission("apps.view")),
    db: Session = Depends(get_db),
) -> AppOut:
    return apps.to_out(_get_app(db, app_id))


@router.patch("/{app_id}")
def update_app(
    app_id: int,
    body: AppUpdate,
    request: Request,
    user: User = Depends(require_permission("apps.edit")),
    db: Session = Depends(get_db),
) -> AppOut:
    app = _get_app(db, app_id)
    data = body.model_dump(exclude_unset=True)
    env = data.pop("environment", None)
    args = data.pop("arguments", None)
    code = data.pop("code", None)
    for key, value in data.items():
        setattr(app, key, value)
    if args is not None:
        app.arguments_json = json.dumps(args)
    if env is not None:
        apps.set_environment(app, env)
    # インラインコードの更新（管理スクリプトへ書き込み）
    if code is not None and app.application_type in ("python_script", "shell_script"):
        apps.write_app_code(app, code)
    # 検証（更新後の値で AppCreate 相当を再チェック）
    try:
        apps.validate_fields(
            AppCreate(
                name=app.name,
                application_type=app.application_type,  # type: ignore[arg-type]
                working_directory=app.working_directory,
                executable_path=app.executable_path,
                script_path=app.script_path,
                python_path=app.python_path,
                url=app.url,
                arguments=json.loads(app.arguments_json or "[]"),
                environment=apps.get_environment(app),
                restart_policy=app.restart_policy,  # type: ignore[arg-type]
                stop_timeout_seconds=app.stop_timeout_seconds,
                systemd_unit_name=app.systemd_unit_name or None,
            )
        )
        apps.sync_unit(app)
    except (apps.AppValidationError, ValueError, OSError) as e:
        db.rollback()
        raise HTTPException(status_code=422, detail=str(e))
    db.commit()
    audit.record(db, "app.update", user=user, resource_type="app", resource_id=str(app_id), request=request)
    return apps.to_out(app)


@router.delete("/{app_id}")
def delete_app(
    app_id: int,
    request: Request,
    user: User = Depends(require_permission("apps.delete")),
    db: Session = Depends(get_db),
):
    app = _get_app(db, app_id)
    if app.application_type != "systemd_service" and app.systemd_unit_name:
        sd.stop(app.systemd_unit_name)
        try:
            sd.remove_unit(app.systemd_unit_name)
        except ValueError:
            pass
    name = app.name
    db.delete(app)
    db.commit()
    audit.record(db, "app.delete", user=user, resource_type="app", resource_id=str(app_id), request=request, metadata={"name": name})
    return {"ok": True}


def _control(
    action: str,
    app_id: int,
    request: Request,
    user: User,
    db: Session,
    fn,
) -> AppOut:
    app = _get_app(db, app_id)
    if not app.systemd_unit_name:
        raise HTTPException(status_code=409, detail="このアプリには systemd ユニットがありません")
    ok, err = fn(app.systemd_unit_name)
    audit.record(
        db,
        f"app.{action}",
        user=user,
        resource_type="app",
        resource_id=str(app_id),
        result="success" if ok else "failure",
        request=request,
        metadata={"name": app.name} | ({} if ok else {"error": err[:500]}),
    )
    if not ok:
        raise HTTPException(status_code=502, detail=f"{action} に失敗しました: {err or '詳細はログを確認してください'}")
    out = apps.to_out(app)
    app.status = out.runtime.status
    db.commit()
    return out


@router.post("/{app_id}/start")
def start_app(app_id: int, request: Request, user: User = Depends(require_permission("apps.start")), db: Session = Depends(get_db)) -> AppOut:
    app = _get_app(db, app_id)
    if app.application_type != "systemd_service":
        try:
            apps.sync_unit(app)  # 起動前に定義を最新化
        except (ValueError, OSError) as e:
            raise HTTPException(status_code=422, detail=str(e))
    sd.reset_failed(app.systemd_unit_name)
    return _control("start", app_id, request, user, db, sd.start)


@router.post("/{app_id}/stop")
def stop_app(app_id: int, request: Request, user: User = Depends(require_permission("apps.stop")), db: Session = Depends(get_db)) -> AppOut:
    return _control("stop", app_id, request, user, db, sd.stop)


@router.post("/{app_id}/restart")
def restart_app(app_id: int, request: Request, user: User = Depends(require_permission("apps.start")), db: Session = Depends(get_db)) -> AppOut:
    return _control("restart", app_id, request, user, db, sd.restart)


@router.post("/{app_id}/kill")
def kill_app(app_id: int, request: Request, user: User = Depends(require_permission("apps.stop")), db: Session = Depends(get_db)) -> AppOut:
    return _control("kill", app_id, request, user, db, sd.kill)
