"""Alembic startup migration with safe SQLite legacy adoption."""
from __future__ import annotations

import fcntl
import hashlib
import json
import logging
import os
import shutil
import sqlite3
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import inspect, text

from app.database import Base, engine

logger = logging.getLogger("control_deck.database.migrations")
_MIN_FREE_BYTES = 16 * 1024 * 1024


def _alembic_config() -> Config:
    backend_root = Path(__file__).resolve().parents[2]
    config_path = backend_root / "alembic.ini"
    config = Config(str(config_path))
    config.set_main_option("script_location", str(backend_root / "alembic"))
    return config


def head_revision() -> str:
    head = ScriptDirectory.from_config(_alembic_config()).get_current_head()
    if not head:
        raise RuntimeError("Alembic head revisionが定義されていません")
    return head


def _sqlite_path() -> Path | None:
    if engine.url.get_backend_name() != "sqlite":
        return None
    database = engine.url.database
    if not database or database == ":memory:":
        return None
    raw = Path(database).expanduser()
    return raw.resolve()


@contextmanager
def _migration_lock() -> Iterator[None]:
    database = _sqlite_path()
    if database is None:
        yield
        return
    database.parent.mkdir(parents=True, exist_ok=True)
    lock_path = database.parent / f".{database.name}.migration.lock"
    flags = os.O_RDWR | os.O_CREAT
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    fd = os.open(lock_path, flags, 0o600)
    try:
        os.fchmod(fd, 0o600)
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


def _table_names() -> set[str]:
    return set(inspect(engine).get_table_names())


def _current_revisions() -> list[str]:
    if "alembic_version" not in _table_names():
        return []
    with engine.connect() as connection:
        return [str(row[0]) for row in connection.execute(text("SELECT version_num FROM alembic_version"))]


def _sqlite_check(connection: sqlite3.Connection) -> None:
    integrity = connection.execute("PRAGMA integrity_check").fetchone()
    if integrity is None or str(integrity[0]).lower() != "ok":
        raise RuntimeError(f"SQLite integrity_check failed: {integrity[0] if integrity else 'no result'}")
    foreign_keys = connection.execute("PRAGMA foreign_key_check").fetchmany(20)
    if foreign_keys:
        raise RuntimeError(f"SQLite foreign_key_check failed: {foreign_keys[:3]}")


def _sqlite_counts(connection: sqlite3.Connection) -> dict[str, int]:
    tables = [
        str(row[0]) for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
        )
    ]
    counts: dict[str, int] = {}
    for table in tables:
        escaped = table.replace('"', '""')
        counts[table] = int(connection.execute(f'SELECT COUNT(*) FROM "{escaped}"').fetchone()[0])
    return counts


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _backup_sqlite(label: str = "pre-alembic") -> Path | None:
    source_path = _sqlite_path()
    if source_path is None or not source_path.exists():
        return None
    if not source_path.is_file():
        raise RuntimeError("SQLite database pathが通常ファイルではありません")
    required = max(_MIN_FREE_BYTES, source_path.stat().st_size * 2)
    if shutil.disk_usage(source_path.parent).free < required:
        raise RuntimeError(f"migration backup用の空き容量が不足しています（必要: {required} bytes）")

    backup_dir = source_path.parent / "migration-backups"
    backup_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(backup_dir, 0o700)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    safe_label = "".join(character for character in label if character.isalnum() or character in "-_")[:64]
    backup_path = backup_dir / f"{source_path.stem}-{safe_label or 'pre-upgrade'}-{stamp}.db"
    suffix = 1
    while backup_path.exists():
        backup_path = backup_dir / f"{source_path.stem}-{safe_label or 'pre-upgrade'}-{stamp}-{suffix}.db"
        suffix += 1

    source = sqlite3.connect(str(source_path))
    destination = sqlite3.connect(str(backup_path))
    try:
        source.execute("PRAGMA busy_timeout=5000")
        _sqlite_check(source)
        source_counts = _sqlite_counts(source)
        with destination:
            source.backup(destination)
        _sqlite_check(destination)
        backup_counts = _sqlite_counts(destination)
        if source_counts != backup_counts:
            raise RuntimeError("migration backupのtable row countが元DBと一致しません")
    except Exception:
        destination.close()
        source.close()
        if backup_path.exists() and backup_path.is_file():
            backup_path.unlink()
        raise
    else:
        destination.close()
        source.close()

    os.chmod(backup_path, 0o600)
    manifest_path = backup_path.with_suffix(".manifest.json")
    manifest: dict[str, Any] = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "source": str(source_path),
        "backup": backup_path.name,
        "sha256": _sha256(backup_path),
        "size_bytes": backup_path.stat().st_size,
        "table_row_counts": backup_counts,
        "target_revision": head_revision(),
    }
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    os.chmod(manifest_path, 0o600)
    logger.info("legacy SQLite migration backup verified: %s", backup_path)
    return backup_path


def verify_schema() -> None:
    """Verify revision, model columns, and SQLite readability after migration."""
    expected_revision = head_revision()
    revisions = _current_revisions()
    if revisions != [expected_revision]:
        raise RuntimeError(f"database revision mismatch: expected={expected_revision} actual={revisions}")
    inspector = inspect(engine)
    actual_tables = set(inspector.get_table_names())
    missing_tables = sorted(set(Base.metadata.tables) - actual_tables)
    missing_columns: list[str] = []
    for table_name, table in Base.metadata.tables.items():
        if table_name not in actual_tables:
            continue
        actual_columns = {column["name"] for column in inspector.get_columns(table_name)}
        missing_columns.extend(
            f"{table_name}.{column.name}" for column in table.columns if column.name not in actual_columns
        )
    if missing_tables or missing_columns:
        raise RuntimeError(
            f"migration schema verification failed: missing_tables={missing_tables} "
            f"missing_columns={missing_columns}"
        )
    database = _sqlite_path()
    if database is not None:
        connection = sqlite3.connect(str(database))
        try:
            _sqlite_check(connection)
            _sqlite_counts(connection)
        finally:
            connection.close()


def migrate_database(prepare_legacy: Callable[[], None]) -> None:
    """Upgrade a managed DB, or safely adopt a pre-Alembic installation once."""
    backup_path: Path | None = None
    with _migration_lock():
        tables = _table_names()
        legacy_tables = tables - {"alembic_version"}
        revisions = _current_revisions()
        try:
            if legacy_tables and not revisions:
                backup_path = _backup_sqlite("pre-alembic")
                prepare_legacy()
                command.stamp(_alembic_config(), "head")
                logger.info("existing database adopted at Alembic revision %s", head_revision())
            else:
                if legacy_tables and revisions != [head_revision()]:
                    current = "-".join(revisions) if revisions else "unversioned"
                    backup_path = _backup_sqlite(f"pre-upgrade-{current}")
                command.upgrade(_alembic_config(), "head")
            verify_schema()
        except Exception as exc:
            detail = f"。退避DB: {backup_path}" if backup_path else ""
            raise RuntimeError(f"データベースmigrationに失敗しました{detail}") from exc
