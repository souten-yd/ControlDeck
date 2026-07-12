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
    _apply_light_migrations()


def _apply_light_migrations() -> None:
    """SQLite 向けの軽量マイグレーション（不足カラムを ADD COLUMN で補う）。

    Alembic 導入までの暫定。カラム追加のみを冪等に行う。
    """
    from sqlalchemy import inspect, text

    if not engine.url.drivername.startswith("sqlite"):
        return
    inspector = inspect(engine)
    # (テーブル, カラム, 型定義)
    additions = [
        ("users", "recovery_codes_encrypted", "TEXT"),
    ]
    with engine.begin() as conn:
        for table, column, coltype in additions:
            if table not in inspector.get_table_names():
                continue
            existing = {c["name"] for c in inspector.get_columns(table)}
            if column not in existing:
                conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {coltype}"))


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
