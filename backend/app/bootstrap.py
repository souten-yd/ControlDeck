"""DB 初期化・ロールシード・管理者作成。"""
from __future__ import annotations

import json

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import Base, engine
from app.models import ManagedApplication, Role, User
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
        ("managed_applications", "url", "VARCHAR(2048)"),
        ("managed_applications", "web_port", "INTEGER"),
        ("remote_connections", "is_self", "BOOLEAN DEFAULT 0"),
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


def seed_repair_app(db: Session) -> None:
    """Control Deck 自身を Claude で修復するためのアプリを登録（冪等）。

    起動すると ~/ControlDeck 上で Claude Code を tmux(cdterm-claude) で立ち上げ、
    Web ターミナルからアタッチして改修できる。再起動後も残る。
    """
    from app.applications import service as apps
    from app.applications import systemd as sd
    from app.config import REPO_ROOT

    name = "Claude 修復コンソール"
    existing = db.execute(
        select(ManagedApplication).where(ManagedApplication.name == name)
    ).scalar_one_or_none()
    if existing is not None:
        return
    script = REPO_ROOT / "scripts" / "claude-repair.sh"
    if not script.exists():
        return
    app = ManagedApplication(
        name=name,
        description="起動すると Claude Code が Web ターミナルの 'claude' セッションに現れ、Control Deck を改修できます。",
        application_type="shell_script",
        script_path=str(script),
        working_directory=str(REPO_ROOT),
        arguments_json="[]",
        restart_policy="no",
    )
    db.add(app)
    db.flush()
    app.systemd_unit_name = sd.unit_name_for(app.id)
    db.commit()
    try:
        apps.sync_unit(app)
    except (ValueError, OSError):
        pass


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
