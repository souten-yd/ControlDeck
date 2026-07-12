import os
import tempfile
from pathlib import Path

import pytest

# app import 前にテスト用の隔離環境を設定する
_tmp = tempfile.mkdtemp(prefix="cd-test-")
_config = Path(_tmp) / "config.yaml"
_sandbox = Path(_tmp) / "sandbox"
_sandbox.mkdir()
_config.write_text(
    f"""
data_dir: {_tmp}/data
server:
  host: 127.0.0.1
  port: 18765
files:
  allowed_roots:
    - {_sandbox}
""",
    encoding="utf-8",
)
os.environ["CONTROL_DECK_CONFIG"] = str(_config)
os.environ["CONTROL_DECK_DB_URL"] = f"sqlite:///{_tmp}/test.db"

from fastapi.testclient import TestClient  # noqa: E402

from app.bootstrap import create_admin, init_db, seed_roles  # noqa: E402
from app.database import SessionLocal  # noqa: E402
from app.main import app  # noqa: E402

CSRF_HEADERS = {"X-Requested-With": "ControlDeck"}


@pytest.fixture(scope="session")
def client():
    init_db()
    db = SessionLocal()
    try:
        seed_roles(db)
        create_admin(db, "admin", "test-password-123")
    finally:
        db.close()
    with TestClient(app) as c:
        yield c


@pytest.fixture()
def admin_client(client):
    r = client.post(
        "/api/v1/auth/login",
        json={"username": "admin", "password": "test-password-123"},
        headers=CSRF_HEADERS,
    )
    assert r.status_code == 200, r.text
    return client
