from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.audit import service as audit
from app.database import get_db
from app.models import Role, User, UserSession, utcnow
from app.schemas.access import (
    ManagedRoleCreate,
    ManagedRoleOut,
    ManagedRoleUpdate,
    ManagedUserCreate,
    ManagedUserOut,
    ManagedUserUpdate,
)
from app.security.deps import require_permission, user_permissions
from app.security.passwords import hash_password
from app.security.permissions import ALL_PERMISSIONS, ROLE_PRESETS

router = APIRouter(tags=["access"])
manage_users = require_permission("users.manage")


def _role_permissions(role: Role) -> set[str]:
    try:
        value = json.loads(role.permissions_json)
    except (TypeError, json.JSONDecodeError):
        return set()
    return {item for item in value if isinstance(item, str)} if isinstance(value, list) else set()


def _validate_permissions(permissions: list[str], actor: User) -> list[str]:
    normalized = list(dict.fromkeys(permissions))
    unknown = sorted(set(normalized) - set(ALL_PERMISSIONS))
    if unknown:
        raise HTTPException(status_code=422, detail=f"未知の権限です: {', '.join(unknown)}")
    excessive = sorted(set(normalized) - user_permissions(actor))
    if excessive:
        raise HTTPException(status_code=403, detail="自分が持たない権限は付与できません")
    return sorted(normalized)


def _ensure_assignable(role: Role, actor: User) -> None:
    if not _role_permissions(role).issubset(user_permissions(actor)):
        raise HTTPException(status_code=403, detail="自分より強いロールは割り当てできません")


def _user_out(user: User) -> ManagedUserOut:
    return ManagedUserOut(
        id=user.id,
        username=user.username,
        display_name=user.display_name or "",
        role_id=user.role_id,
        role_name=user.role.name,
        is_active=user.is_active,
        totp_enabled=user.totp_enabled,
        created_at=user.created_at,
        last_login_at=user.last_login_at,
    )


def _role_out(db: Session, role: Role) -> ManagedRoleOut:
    count = db.scalar(select(func.count()).select_from(User).where(User.role_id == role.id)) or 0
    return ManagedRoleOut(
        id=role.id,
        name=role.name,
        permissions=sorted(_role_permissions(role)),
        preset=role.name in ROLE_PRESETS,
        user_count=count,
    )


def _revoke_user_sessions(db: Session, user_id: int) -> None:
    db.execute(
        update(UserSession)
        .where(UserSession.user_id == user_id, UserSession.revoked_at.is_(None))
        .values(revoked_at=utcnow())
    )


def _last_active_administrator(db: Session, user: User) -> bool:
    if user.role.name != "administrator" or not user.is_active:
        return False
    count = db.scalar(
        select(func.count())
        .select_from(User)
        .join(Role, User.role_id == Role.id)
        .where(User.is_active.is_(True), Role.name == "administrator")
    ) or 0
    return count <= 1


@router.get("/users", response_model=list[ManagedUserOut])
def list_users(
    actor: User = Depends(manage_users), db: Session = Depends(get_db),
) -> list[ManagedUserOut]:
    rows = db.execute(select(User).order_by(User.username)).scalars().all()
    return [_user_out(row) for row in rows]


@router.post("/users", response_model=ManagedUserOut, status_code=201)
def create_user(
    body: ManagedUserCreate,
    request: Request,
    actor: User = Depends(manage_users),
    db: Session = Depends(get_db),
) -> ManagedUserOut:
    role = db.get(Role, body.role_id)
    if role is None:
        raise HTTPException(status_code=422, detail="ロールが見つかりません")
    _ensure_assignable(role, actor)
    row = User(
        username=body.username,
        display_name=body.display_name.strip(),
        password_hash=hash_password(body.password),
        role_id=role.id,
        is_active=True,
    )
    db.add(row)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="同じユーザー名が既に存在します") from None
    db.refresh(row)
    audit.record(
        db, "user.create", user=actor, resource_type="user", resource_id=str(row.id), request=request,
        metadata={"username": row.username, "role": role.name},
    )
    return _user_out(row)


@router.patch("/users/{user_id}", response_model=ManagedUserOut)
def update_user(
    user_id: int,
    body: ManagedUserUpdate,
    request: Request,
    actor: User = Depends(manage_users),
    db: Session = Depends(get_db),
) -> ManagedUserOut:
    row = db.get(User, user_id)
    if row is None:
        raise HTTPException(status_code=404, detail="ユーザーが見つかりません")
    _ensure_assignable(row.role, actor)
    if row.id == actor.id and (
        (body.role_id is not None and body.role_id != row.role_id)
        or body.is_active is False
        or body.new_password is not None
    ):
        raise HTTPException(status_code=409, detail="自分自身のロール・有効状態・パスワードはこの画面から変更できません")

    target_role = row.role
    if body.role_id is not None:
        target_role = db.get(Role, body.role_id)
        if target_role is None:
            raise HTTPException(status_code=422, detail="ロールが見つかりません")
        _ensure_assignable(target_role, actor)
    removing_last_admin = _last_active_administrator(db, row) and (
        (body.role_id is not None and target_role.name != "administrator") or body.is_active is False
    )
    if removing_last_admin:
        raise HTTPException(status_code=409, detail="最後の有効な管理者は無効化・降格できません")

    changed: list[str] = []
    revoke = False
    if body.display_name is not None and body.display_name.strip() != row.display_name:
        row.display_name = body.display_name.strip()
        changed.append("display_name")
    if body.role_id is not None and body.role_id != row.role_id:
        row.role_id = target_role.id
        changed.append("role")
        revoke = True
    if body.is_active is not None and body.is_active != row.is_active:
        row.is_active = body.is_active
        changed.append("is_active")
        revoke = True
    if body.new_password is not None:
        row.password_hash = hash_password(body.new_password)
        changed.append("password")
        revoke = True
    if not changed:
        raise HTTPException(status_code=400, detail="変更内容がありません")
    if revoke:
        _revoke_user_sessions(db, row.id)
    db.commit()
    db.refresh(row)
    audit.record(
        db, "user.update", user=actor, resource_type="user", resource_id=str(row.id), request=request,
        metadata={"username": row.username, "fields": changed, "sessions_revoked": revoke},
    )
    return _user_out(row)


@router.get("/roles", response_model=list[ManagedRoleOut])
def list_roles(
    actor: User = Depends(manage_users), db: Session = Depends(get_db),
) -> list[ManagedRoleOut]:
    rows = db.execute(select(Role).order_by(Role.name)).scalars().all()
    actor_permissions = user_permissions(actor)
    return [_role_out(db, row) for row in rows if _role_permissions(row).issubset(actor_permissions)]


@router.get("/roles/permissions", response_model=list[str])
def list_permissions(actor: User = Depends(manage_users)) -> list[str]:
    return sorted(user_permissions(actor))


@router.post("/roles", response_model=ManagedRoleOut, status_code=201)
def create_role(
    body: ManagedRoleCreate,
    request: Request,
    actor: User = Depends(manage_users),
    db: Session = Depends(get_db),
) -> ManagedRoleOut:
    if body.name in ROLE_PRESETS:
        raise HTTPException(status_code=409, detail="プリセット名は使用できません")
    permissions = _validate_permissions(body.permissions, actor)
    role = Role(name=body.name, permissions_json=json.dumps(permissions))
    db.add(role)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="同じロール名が既に存在します") from None
    db.refresh(role)
    audit.record(
        db, "role.create", user=actor, resource_type="role", resource_id=str(role.id), request=request,
        metadata={"name": role.name, "permission_count": len(permissions)},
    )
    return _role_out(db, role)


@router.patch("/roles/{role_id}", response_model=ManagedRoleOut)
def update_role(
    role_id: int,
    body: ManagedRoleUpdate,
    request: Request,
    actor: User = Depends(manage_users),
    db: Session = Depends(get_db),
) -> ManagedRoleOut:
    role = db.get(Role, role_id)
    if role is None:
        raise HTTPException(status_code=404, detail="ロールが見つかりません")
    if role.name in ROLE_PRESETS:
        raise HTTPException(status_code=409, detail="プリセットロールは変更できません")
    _ensure_assignable(role, actor)
    if actor.role_id == role.id:
        raise HTTPException(status_code=409, detail="自分自身のロールは変更できません")
    permissions = _validate_permissions(body.permissions, actor)
    role.permissions_json = json.dumps(permissions)
    user_ids = db.execute(select(User.id).where(User.role_id == role.id)).scalars().all()
    if user_ids:
        db.execute(
            update(UserSession)
            .where(UserSession.user_id.in_(user_ids), UserSession.revoked_at.is_(None))
            .values(revoked_at=utcnow())
        )
    db.commit()
    audit.record(
        db, "role.update", user=actor, resource_type="role", resource_id=str(role.id), request=request,
        metadata={"name": role.name, "permission_count": len(permissions), "sessions_revoked": len(user_ids)},
    )
    return _role_out(db, role)


@router.delete("/roles/{role_id}")
def delete_role(
    role_id: int,
    request: Request,
    actor: User = Depends(manage_users),
    db: Session = Depends(get_db),
):
    role = db.get(Role, role_id)
    if role is None:
        raise HTTPException(status_code=404, detail="ロールが見つかりません")
    if role.name in ROLE_PRESETS:
        raise HTTPException(status_code=409, detail="プリセットロールは削除できません")
    _ensure_assignable(role, actor)
    if db.scalar(select(func.count()).select_from(User).where(User.role_id == role.id)):
        raise HTTPException(status_code=409, detail="利用中のロールは削除できません")
    name = role.name
    db.delete(role)
    db.commit()
    audit.record(
        db, "role.delete", user=actor, resource_type="role", resource_id=str(role_id), request=request,
        metadata={"name": name},
    )
    return {"ok": True}
