"""DB 初期化・ロールシード・管理者作成。"""
from __future__ import annotations

import json
import logging
import subprocess
import hashlib
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import Base, engine
from app.models import ManagedApplication, Role, User, Workflow, WorkflowVersion, utcnow
from app.security.passwords import hash_password
from app.security.permissions import ROLE_PRESETS

logger = logging.getLogger("control_deck.bootstrap")


def init_db() -> None:
    Base.metadata.create_all(engine)
    _apply_light_migrations()
    _publish_legacy_enabled_workflows()
    _backfill_workflow_contracts()


def _publish_legacy_enabled_workflows() -> None:
    """公開境界導入前から有効な自動実行workflowだけを一度baseline化する。"""
    from app.workflows.engine import safe_definition_snapshot
    from app.database import SessionLocal

    with SessionLocal() as db:
        workflows = db.execute(select(Workflow).where(Workflow.enabled.is_(True))).scalars().all()
        migrated = 0
        for workflow in workflows:
            published = db.execute(select(WorkflowVersion.id).where(
                WorkflowVersion.workflow_id == workflow.id, WorkflowVersion.published_at.is_not(None),
            ).limit(1)).scalar_one_or_none()
            if published is not None:
                continue
            latest = db.execute(select(WorkflowVersion.version).where(
                WorkflowVersion.workflow_id == workflow.id,
            ).order_by(WorkflowVersion.version.desc()).limit(1)).scalar_one_or_none() or 0
            definition = workflow.definition_json or "{}"
            db.add(WorkflowVersion(
                workflow_id=workflow.id, version=latest + 1, name=workflow.name,
                description=workflow.description,
                definition_json=json.dumps(safe_definition_snapshot(json.loads(definition)), ensure_ascii=False),
                checksum=hashlib.sha256(definition.encode()).hexdigest(), note="公開境界導入時のlegacy baseline",
                published_at=utcnow(),
            ))
            migrated += 1
        if migrated:
            db.commit()
            logger.info("legacy enabled workflows published as baseline: %s", migrated)


def _backfill_workflow_contracts() -> None:
    """既存公開版へ表示説明と生成可能な入出力contractを補完する。"""
    from app.database import SessionLocal
    from app.workflows.contracts import build_input_schema, build_output_schema

    with SessionLocal() as db:
        versions = db.execute(select(WorkflowVersion).where(
            WorkflowVersion.published_at.is_not(None),
        )).scalars().all()
        changed = 0
        for version in versions:
            workflow = db.get(Workflow, version.workflow_id)
            if workflow is None:
                continue
            try:
                definition = json.loads(version.definition_json or "{}")
            except json.JSONDecodeError:
                continue
            if not version.description:
                version.description = workflow.description
                changed += 1
            try:
                input_schema = json.loads(version.input_schema_json or "{}")
            except json.JSONDecodeError:
                input_schema = {}
            if not input_schema:
                version.input_schema_json = json.dumps(build_input_schema(definition), ensure_ascii=False)
                changed += 1
            try:
                output_schema = json.loads(version.output_schema_json or "{}")
            except json.JSONDecodeError:
                output_schema = {}
            if not output_schema:
                version.output_schema_json = json.dumps(build_output_schema(definition), ensure_ascii=False)
                changed += 1
        if changed:
            db.commit()
            logger.info("published workflow contracts backfilled: %s", changed)


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
        ("project_runs", "web_port", "INTEGER"),
        ("users", "recovery_codes_encrypted", "TEXT"),
        ("managed_applications", "url", "VARCHAR(2048)"),
        ("managed_applications", "web_port", "INTEGER"),
        ("managed_applications", "health_check_json", "TEXT DEFAULT '{}'"),
        ("remote_connections", "is_self", "BOOLEAN DEFAULT 0"),
        ("workflow_versions", "version", "INTEGER DEFAULT 1"),
        ("workflow_versions", "description", "TEXT DEFAULT ''"),
        ("workflow_versions", "input_schema_json", "TEXT DEFAULT '{}'"),
        ("workflow_versions", "output_schema_json", "TEXT DEFAULT '{}'"),
        ("workflow_versions", "checksum", "VARCHAR(64) DEFAULT ''"),
        ("workflow_versions", "published_at", "DATETIME"),
        ("workflow_executions", "workflow_version_id", "INTEGER"),
        ("workflow_executions", "definition_snapshot_json", "TEXT DEFAULT '{}'"),
        ("workflow_executions", "runtime_snapshot_json", "TEXT DEFAULT '{}'"),
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


def remove_retired_repair_app(db: Session) -> int:
    """旧seed由来のClaude修復コンソールと管理unitを一度だけ撤去する。"""
    from app.applications import health as app_health
    from app.applications import systemd as sd
    from app.audit import service as audit

    name = "Claude 修復コンソール"
    candidates = db.execute(
        select(ManagedApplication).where(ManagedApplication.name == name)
    ).scalars().all()
    retired = [
        app for app in candidates
        if app.application_type == "shell_script"
        and Path(app.script_path or "").name == "claude-repair.sh"
    ]
    for app in retired:
        if app.systemd_unit_name:
            try:
                sd.stop(app.systemd_unit_name)
                sd.remove_unit(app.systemd_unit_name)
            except (OSError, ValueError, subprocess.SubprocessError) as exc:
                logger.warning("旧Claude修復unitの撤去に失敗しました: %s", exc)
        app_id = app.id
        app_health.clear(app_id)
        db.delete(app)
        db.commit()
        audit.record(
            db, "app.retired_remove", resource_type="app", resource_id=str(app_id),
            metadata={"name": name},
        )
    return len(retired)


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
