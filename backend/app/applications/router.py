from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.applications import service as apps
from app.applications import systemd as sd
from app.applications.discovery import discover_project, discover_pythons
from app.audit import service as audit
from app.database import get_db
from app.models import ManagedApplication, User
from app.schemas.apps import AppCreate, AppOut, AppUpdate
from app.security.deps import require_permission

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
        arguments_json=json.dumps(body.arguments),
        auto_start=body.auto_start,
        restart_policy=body.restart_policy,
        stop_timeout_seconds=body.stop_timeout_seconds,
    )
    apps.set_environment(app, body.environment)
    db.add(app)
    db.flush()
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
    for key, value in data.items():
        setattr(app, key, value)
    if args is not None:
        app.arguments_json = json.dumps(args)
    if env is not None:
        apps.set_environment(app, env)
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
