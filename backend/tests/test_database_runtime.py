from __future__ import annotations

import io
import os
import tarfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.database.runtime import (
    describe_database_url,
    normalized_database_url,
    read_database_env,
    validate_database_url,
)


def test_database_url_accepts_only_sqlite_and_psycopg_without_exposing_password():
    normalized = normalized_database_url("postgresql://user:secret@db.internal/control")
    assert normalized.startswith("postgresql+psycopg://")
    description = describe_database_url(normalized)
    assert description == "backend=postgresql host=db.internal port=5432 database=control"
    assert "secret" not in description

    with pytest.raises(ValueError):
        validate_database_url("mysql://user:secret@db/control")
    with pytest.raises(ValueError):
        validate_database_url("postgresql+psycopg2://user:secret@db/control")
    with pytest.raises(ValueError):
        validate_database_url("postgresql://user:secret@db/")
    with pytest.raises(ValueError, match="絶対path"):
        normalized_database_url("sqlite:///relative.db")
    with pytest.raises(ValueError, match="制御文字"):
        validate_database_url("postgresql://user:secret@db/control%0Adeck")


def test_database_environment_file_requires_owned_0600_regular_file(tmp_path: Path):
    env_file = tmp_path / "database.env"
    env_file.write_text("CONTROL_DECK_DB_URL=postgresql://user:secret@db/control\n", encoding="utf-8")
    env_file.chmod(0o600)
    value = read_database_env(env_file, required=True)
    assert value == "postgresql+psycopg://user:secret@db/control"

    env_file.chmod(0o640)
    with pytest.raises(ValueError, match="0600"):
        read_database_env(env_file, required=True)

    env_file.chmod(0o600)
    link = tmp_path / "database-link.env"
    link.symlink_to(env_file)
    with pytest.raises(ValueError, match="symlink"):
        read_database_env(link, required=True)


def test_database_environment_file_rejects_extra_assignments(tmp_path: Path):
    env_file = tmp_path / "database.env"
    env_file.write_text(
        "CONTROL_DECK_DB_URL=postgresql://user:secret@db/control\nOTHER=value\n",
        encoding="utf-8",
    )
    env_file.chmod(0o600)
    with pytest.raises(ValueError, match="1行"):
        read_database_env(env_file, required=True)
    assert read_database_env(tmp_path / "missing.env") is None


def test_database_environment_owner_is_current_user(tmp_path: Path, monkeypatch):
    env_file = tmp_path / "database.env"
    env_file.write_text("CONTROL_DECK_DB_URL=sqlite:///:memory:\n", encoding="utf-8")
    env_file.chmod(0o600)
    monkeypatch.setattr(os, "getuid", lambda: env_file.stat().st_uid + 1)
    with pytest.raises(ValueError, match="owner"):
        read_database_env(env_file, required=True)


def test_pg_dump_uses_fixed_argv_and_keeps_password_out_of_arguments(tmp_path: Path, monkeypatch):
    from app.database import pg_tools

    captured: dict = {}

    def run(argv, **kwargs):
        captured.update({"argv": argv, "env": kwargs["env"], "pass_fds": kwargs["pass_fds"]})
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(pg_tools, "db_url", lambda: "postgresql+psycopg://user:secret@db.internal/control")
    monkeypatch.setattr(pg_tools, "_tool", lambda name: f"/fixed/{name}")
    monkeypatch.setattr(pg_tools.subprocess, "run", run)
    output = tmp_path / "backup.dump"
    pg_tools.dump(output)

    assert output.exists() and output.stat().st_mode & 0o777 == 0o600
    assert captured["argv"][0] == "/fixed/pg_dump"
    assert "secret" not in " ".join(captured["argv"])
    assert captured["env"]["PGPASSWORD"] == "secret"
    assert captured["pass_fds"]


def test_pg_dump_failure_removes_partial_output(tmp_path: Path, monkeypatch):
    from app.database import pg_tools

    monkeypatch.setattr(pg_tools, "db_url", lambda: "postgresql+psycopg://user:secret@db/control")
    monkeypatch.setattr(pg_tools, "_tool", lambda name: f"/fixed/{name}")
    monkeypatch.setattr(
        pg_tools.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(returncode=1),
    )
    output = tmp_path / "partial.dump"
    with pytest.raises(RuntimeError, match="pg_dump"):
        pg_tools.dump(output)
    assert not output.exists()


def test_pg_restore_rejects_symlink_input(tmp_path: Path, monkeypatch):
    from app.database import pg_tools

    dump = tmp_path / "backup.dump"
    dump.write_bytes(b"fixture")
    link = tmp_path / "backup-link.dump"
    link.symlink_to(dump)
    monkeypatch.setattr(pg_tools, "db_url", lambda: "postgresql+psycopg://user:secret@db/control")
    monkeypatch.setattr(pg_tools, "_tool", lambda name: f"/fixed/{name}")
    with pytest.raises(OSError):
        pg_tools.restore(link)


def test_backup_archive_extracts_regular_known_root(tmp_path: Path):
    from app.database.backup_archive import extract_backup

    archive = tmp_path / "backup.tar.gz"
    payload = b"manifest"
    with tarfile.open(archive, "w:gz") as output:
        root = tarfile.TarInfo("control-deck-backup")
        root.type = tarfile.DIRTYPE
        output.addfile(root)
        member = tarfile.TarInfo("control-deck-backup/MANIFEST.txt")
        member.size = len(payload)
        output.addfile(member, io.BytesIO(payload))
    destination = tmp_path / "extract"
    destination.mkdir()
    extract_backup(archive, destination)
    assert (destination / "control-deck-backup" / "MANIFEST.txt").read_bytes() == payload


@pytest.mark.parametrize("kind", ["traversal", "symlink"])
def test_backup_archive_rejects_escape_and_links(tmp_path: Path, kind: str):
    from app.database.backup_archive import extract_backup

    archive = tmp_path / f"{kind}.tar.gz"
    with tarfile.open(archive, "w:gz") as output:
        member = tarfile.TarInfo(
            "control-deck-backup/../../escape" if kind == "traversal" else "control-deck-backup/link"
        )
        if kind == "symlink":
            member.type = tarfile.SYMTYPE
            member.linkname = "/etc/passwd"
        output.addfile(member, io.BytesIO(b"") if kind == "traversal" else None)
    destination = tmp_path / "extract"
    destination.mkdir()
    with pytest.raises(ValueError):
        extract_backup(archive, destination)
    assert not (tmp_path / "escape").exists()
