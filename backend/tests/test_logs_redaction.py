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
