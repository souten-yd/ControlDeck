from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path


def _run(database: Path, code: str) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["CONTROL_DECK_DB_URL"] = f"sqlite:///{database}"
    return subprocess.run(
        [sys.executable, "-c", code],
        cwd=Path(__file__).resolve().parents[1],
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )


def _init_code() -> str:
    return """
import json
from sqlalchemy import text
from app.bootstrap import init_db
from app.database import engine
from app.database.migrations import verify_schema
init_db()
verify_schema()
with engine.connect() as connection:
    print(json.dumps({
        "revision": connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one(),
        "roles": connection.execute(text("SELECT COUNT(*) FROM roles")).scalar_one(),
    }))
"""


def test_new_database_upgrades_to_head_without_legacy_backup(tmp_path: Path):
    database = tmp_path / "new.db"
    result = _run(database, _init_code())
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout.strip().splitlines()[-1])
    assert payload["revision"]
    assert payload["roles"] == 0
    assert not (tmp_path / "migration-backups").exists()

    second = _run(database, _init_code())
    assert second.returncode == 0, second.stderr
    assert json.loads(second.stdout.strip().splitlines()[-1]) == payload


def test_legacy_database_is_backed_up_verified_and_adopted_once(tmp_path: Path):
    database = tmp_path / "legacy.db"
    setup = _run(database, """
from sqlalchemy import text
import app.models
from app.database import Base, engine
Base.metadata.create_all(engine)
with engine.begin() as connection:
    connection.execute(text("INSERT INTO roles (name, permissions_json) VALUES ('legacy-role', '[]')"))
""")
    assert setup.returncode == 0, setup.stderr

    result = _run(database, _init_code())
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout.strip().splitlines()[-1])
    assert payload["roles"] == 1

    backup_dir = tmp_path / "migration-backups"
    backups = list(backup_dir.glob("legacy-pre-alembic-*.db"))
    manifests = list(backup_dir.glob("legacy-pre-alembic-*.manifest.json"))
    assert len(backups) == len(manifests) == 1
    assert backups[0].stat().st_mode & 0o777 == 0o600
    assert manifests[0].stat().st_mode & 0o777 == 0o600
    manifest = json.loads(manifests[0].read_text(encoding="utf-8"))
    assert manifest["sha256"] == hashlib.sha256(backups[0].read_bytes()).hexdigest()
    assert manifest["table_row_counts"]["roles"] == 1
    with sqlite3.connect(backups[0]) as connection:
        assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert connection.execute("SELECT COUNT(*) FROM roles").fetchone()[0] == 1

    second = _run(database, _init_code())
    assert second.returncode == 0, second.stderr
    assert len(list(backup_dir.glob("legacy-pre-alembic-*.db"))) == 1


def test_stamped_schema_drift_fails_instead_of_being_silently_hidden(tmp_path: Path):
    database = tmp_path / "drift.db"
    initialized = _run(database, _init_code())
    assert initialized.returncode == 0, initialized.stderr
    with sqlite3.connect(database) as connection:
        connection.execute("ALTER TABLE users DROP COLUMN display_name")
        connection.commit()

    failed = _run(database, _init_code())
    assert failed.returncode != 0
    assert "migration" in failed.stderr.lower()
    with sqlite3.connect(database) as connection:
        columns = {row[1] for row in connection.execute("PRAGMA table_info(users)")}
    assert "display_name" not in columns


def test_versioned_upgrade_creates_verified_pre_upgrade_backup(tmp_path: Path):
    database = tmp_path / "versioned.db"
    baseline = _run(database, """
from alembic import command
from app.database.migrations import _alembic_config
command.upgrade(_alembic_config(), "dd6115224a90")
""")
    assert baseline.returncode == 0, baseline.stderr
    with sqlite3.connect(database) as connection:
        connection.execute("INSERT INTO roles (name, permissions_json) VALUES ('before-upgrade', '[]')")
        connection.commit()

    upgraded = _run(database, _init_code())
    assert upgraded.returncode == 0, upgraded.stderr
    backup_dir = tmp_path / "migration-backups"
    backups = list(backup_dir.glob("versioned-pre-upgrade-dd6115224a90-*.db"))
    manifests = list(backup_dir.glob("versioned-pre-upgrade-dd6115224a90-*.manifest.json"))
    assert len(backups) == len(manifests) == 1
    manifest = json.loads(manifests[0].read_text(encoding="utf-8"))
    assert manifest["sha256"] == hashlib.sha256(backups[0].read_bytes()).hexdigest()
    assert manifest["table_row_counts"]["roles"] == 1
    with sqlite3.connect(backups[0]) as connection:
        assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert connection.execute("SELECT version_num FROM alembic_version").fetchone()[0] == "dd6115224a90"


def test_business_event_outbox_migration_has_delivery_constraints(tmp_path: Path):
    database = tmp_path / "business-events.db"
    upgraded = _run(database, """
from alembic import command
from app.database.migrations import _alembic_config
command.upgrade(_alembic_config(), "c2f8a6d53b91")
""")
    assert upgraded.returncode == 0, upgraded.stderr

    with sqlite3.connect(database) as connection:
        tables = {
            row[0] for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        assert {"workflow_business_events", "workflow_event_deliveries"} <= tables
        event_indexes = {
            row[1]: bool(row[2])
            for row in connection.execute("PRAGMA index_list(workflow_business_events)")
        }
        delivery_indexes = {
            row[1]: bool(row[2])
            for row in connection.execute("PRAGMA index_list(workflow_event_deliveries)")
        }
        assert event_indexes["ix_workflow_business_events_event_id"] is True
        assert any(unique for name, unique in delivery_indexes.items() if name.startswith("sqlite_autoindex"))
        foreign_tables = {
            row[2] for row in connection.execute("PRAGMA foreign_key_list(workflow_event_deliveries)")
        }
        assert {"workflow_business_events", "workflow_executions", "workflows"} <= foreign_tables

    downgraded = _run(database, """
from alembic import command
from app.database.migrations import _alembic_config
command.downgrade(_alembic_config(), "b7e1d94c2f60")
""")
    assert downgraded.returncode == 0, downgraded.stderr
    with sqlite3.connect(database) as connection:
        tables = {
            row[0] for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        assert "workflow_business_events" not in tables
        assert "workflow_event_deliveries" not in tables
