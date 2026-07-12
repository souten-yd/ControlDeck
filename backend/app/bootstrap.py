"""DB 初期化・ロールシード・管理者作成。"""
from __future__ import annotations

import json

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import Base, engine
from app.models import Role, User
from app.security.passwords import hash_password
from app.security.permissions import ROLE_PRESETS


def init_db() -> None:
    Base.metadata.create_all(engine)


def seed_roles(db: Session) -> None:
    for name, perms in ROLE_PRESETS.items():
        existing = db.execute(select(Role).where(Role.name == name)).scalar_one_or_none()
        if existing is None:
            db.add(Role(name=name, permissions_json=json.dumps(perms)))
        else:
            # プリセットロールは定義を最新へ同期
            existing.permissions_json = json.dumps(perms)
    db.commit()


def create_admin(db: Session, username: str, password: str, display_name: str = "") -> User:
    role = db.execute(select(Role).where(Role.name == "administrator")).scalar_one()
    existing = db.execute(select(User).where(User.username == username)).scalar_one_or_none()
    if existing is not None:
        raise ValueError(f"ユーザー {username} は既に存在します")
    user = User(
        username=username,
        display_name=display_name or username,
        password_hash=hash_password(password),
        role_id=role.id,
    )
    db.add(user)
    db.commit()
    return user
