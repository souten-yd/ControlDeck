from __future__ import annotations

from tests.conftest import CSRF_HEADERS


def test_log_api_download_and_websocket_redact_secrets(admin_client):
    from app.logs import service as logs

    secret = "known-runtime-secret-value"
    created = admin_client.post(
        "/api/v1/apps",
        json={
            "name": "redacted logs",
            "application_type": "url_shortcut",
            "url": "https://example.test",
            "environment": {"API_TOKEN": secret},
        },
        headers=CSRF_HEADERS,
    )
    assert created.status_code == 201, created.text
    app_id = created.json()["id"]
    path = logs.log_path(app_id, "stdout")
    path.write_text(
        "safe line\n"
        "password=literal-password\n"
        "Authorization: Bearer auth-token\n"
        f"opaque value {secret}\n"
        'json {"cookie": "cookie with spaces"}\n',
        encoding="utf-8",
    )
    try:
        response = admin_client.get(f"/api/v1/apps/{app_id}/logs?stream=stdout")
        assert response.status_code == 200
        rendered = "\n".join(response.json()["lines"])
        assert "safe line" in rendered
        assert "literal-password" not in rendered
        assert "auth-token" not in rendered
        assert secret not in rendered
        assert "cookie with spaces" not in rendered
        assert rendered.count("***") >= 4

        download = admin_client.get(f"/api/v1/apps/{app_id}/logs/download?stream=stdout")
        assert download.status_code == 200
        assert secret not in download.text and "literal-password" not in download.text
        assert download.headers["content-disposition"] == f'attachment; filename="app-{app_id}-stdout.log"'

        with admin_client.websocket_connect(f"/api/v1/apps/{app_id}/logs/stream?stream=stdout") as websocket:
            initial = websocket.receive_json()
            assert initial["type"] == "initial"
            assert secret not in "\n".join(initial["lines"])
            with path.open("a", encoding="utf-8") as handle:
                handle.write("API_KEY=stream-secret-value\n")
            appended = websocket.receive_json()
            assert appended == {"type": "append", "data": "API_KEY=***\n"}
    finally:
        admin_client.delete(f"/api/v1/apps/{app_id}", headers=CSRF_HEADERS)


def test_redacted_line_buffer_handles_split_and_oversized_lines():
    from app.logs import service as logs

    buffer = logs.RedactedLineBuffer({"known-secret-value"})
    assert buffer.feed(b"API_") == ""
    assert buffer.feed(b"KEY=split-secret\nknown-secret-") == "API_KEY=***\n"
    assert buffer.feed(b"value\n") == "***\n"

    oversized = logs.RedactedLineBuffer()
    marker = oversized.feed(b"password=" + b"x" * logs.MAX_STREAM_LINE_BYTES)
    assert marker == "[1MiBを超える改行なしログ行を省略]\n"
    assert oversized.feed(b"still-secret\nnext=safe\n") == "next=safe\n"

    complete_oversized = logs.RedactedLineBuffer()
    assert complete_oversized.feed(b"secret=" + b"x" * logs.MAX_STREAM_LINE_BYTES + b"\n") == (
        "[1MiBを超える改行なしログ行を省略]\n"
    )


def test_custom_log_file_is_contained_redacted_streamed_and_deleted(admin_client):
    from app.files import service as files

    root = files.allowed_roots()[0]
    path = root / "custom-app.log"
    path.write_text("API_KEY=custom-secret\nsafe custom line\n", encoding="utf-8")
    created = admin_client.post(
        "/api/v1/apps",
        json={
            "name": "custom log source", "application_type": "url_shortcut",
            "url": "https://example.test", "log_files": [str(path)],
        },
        headers=CSRF_HEADERS,
    )
    assert created.status_code == 201, created.text
    app_id = created.json()["id"]
    try:
        assert created.json()["log_files"] == [str(path.resolve())]
        assert admin_client.patch(
            f"/api/v1/apps/{app_id}", json={"log_files": ["/etc/passwd"]}, headers=CSRF_HEADERS,
        ).status_code == 422
        sources = admin_client.get(f"/api/v1/apps/{app_id}/log-sources").json()
        assert {item["id"] for item in sources} == {"stdout", "stderr", "file:0"}
        response = admin_client.get(f"/api/v1/apps/{app_id}/logs?source=file:0")
        assert response.status_code == 200
        assert response.json()["lines"] == ["API_KEY=***", "safe custom line"]
        with admin_client.websocket_connect(f"/api/v1/apps/{app_id}/logs/stream?source=file:0") as websocket:
            assert websocket.receive_json()["lines"] == ["API_KEY=***", "safe custom line"]
        deleted = admin_client.delete(
            f"/api/v1/apps/{app_id}/logs?source=file:0", headers=CSRF_HEADERS,
        )
        assert deleted.status_code == 200 and path.read_text(encoding="utf-8") == ""
    finally:
        admin_client.delete(f"/api/v1/apps/{app_id}", headers=CSRF_HEADERS)
        path.unlink(missing_ok=True)

    rejected = admin_client.post(
        "/api/v1/apps",
        json={
            "name": "outside log", "application_type": "url_shortcut",
            "url": "https://example.test", "log_files": ["/etc/passwd"],
        },
        headers=CSRF_HEADERS,
    )
    assert rejected.status_code == 422


def test_journal_source_uses_registered_unit_and_cannot_be_deleted(admin_client, monkeypatch):
    from app.database import SessionLocal
    from app.models import ManagedApplication

    db = SessionLocal()
    try:
        app = ManagedApplication(
            name="journal source", application_type="systemd_service",
            systemd_unit_name="fixture.service", systemd_scope="user",
        )
        db.add(app)
        db.commit()
        app_id = app.id
    finally:
        db.close()

    calls = []

    def journal(unit, scope, max_lines, sensitive_values=None):
        calls.append((unit, scope, max_lines, sensitive_values))
        return ["password=***", "fixture ready"]

    monkeypatch.setattr("app.logs.service.journal_lines", journal)
    try:
        sources = admin_client.get(f"/api/v1/apps/{app_id}/log-sources").json()
        assert any(item == {"id": "journal", "label": "systemd journal", "kind": "journal", "deletable": False} for item in sources)
        response = admin_client.get(f"/api/v1/apps/{app_id}/logs?source=journal&lines=20")
        assert response.status_code == 200 and response.json()["lines"] == ["password=***", "fixture ready"]
        assert calls == [("fixture.service", "user", 20, set())]
        assert admin_client.delete(
            f"/api/v1/apps/{app_id}/logs?source=journal", headers=CSRF_HEADERS,
        ).status_code == 409
    finally:
        db = SessionLocal()
        try:
            db.delete(db.get(ManagedApplication, app_id))
            db.commit()
        finally:
            db.close()


def test_journal_reader_uses_fixed_argv_and_redacts(monkeypatch):
    from types import SimpleNamespace

    from app.logs import service as logs

    calls = []

    def run(argv, **kwargs):
        calls.append((argv, kwargs))
        return SimpleNamespace(returncode=0, stdout=b"password=journal-secret\nready\n")

    monkeypatch.setattr(logs.subprocess, "run", run)
    assert logs.journal_lines("fixture.service", "user", 50) == ["password=***", "ready"]
    assert calls[0][0][1:] == [
        "--user", "--unit", "fixture.service", "--output=short-iso", "--no-pager", "--lines", "50",
    ]
    assert calls[0][1] == {"capture_output": True, "timeout": 5, "check": False, "shell": False}
    try:
        logs.journal_lines("../../bad.service", "user", 10)
    except ValueError:
        pass
    else:
        raise AssertionError("invalid unit must be rejected")
