import asyncio
import json

import pytest

from tests.conftest import CSRF_HEADERS


def test_encode_instruction():
    from app.remote_desktop.guacd import encode_instruction

    assert encode_instruction("select", "rdp") == b"6.select,3.rdp;"
    assert encode_instruction("size", "1024", "768", "96") == b"4.size,4.1024,3.768,2.96;"
    # マルチバイトも要素数で数える（len は codepoint 数）
    assert encode_instruction("エラー") == "3.エラー;".encode("utf-8")


def test_instruction_parser_partial_and_multiple():
    from app.remote_desktop.guacd import InstructionParser

    p = InstructionParser()
    # 途中まで
    assert p.feed("4.args,13.VERSION_1_1_0") == []
    # 完成 + 次の命令の一部
    got = p.feed(",8.hostname,4.port;3.foo")
    assert got == [["args", "VERSION_1_1_0", "hostname", "port"]]
    # 残りを完成
    assert p.feed(";") == [["foo"]]


def test_parser_multiple_in_one_feed():
    from app.remote_desktop.guacd import InstructionParser

    p = InstructionParser()
    got = p.feed("3.foo;3.bar;")
    assert got == [["foo"], ["bar"]]


def test_build_guacd_params():
    from app.models import RemoteConnection
    from app.remote_desktop import service

    conn = RemoteConnection(name="srv", protocol="rdp", host="10.0.0.5", port=3389, username="admin", params_json='{"color-depth": 16}')
    service.set_secret_params(conn, {"password": "s3cret"})
    params = service.build_guacd_params(conn)
    assert params["hostname"] == "10.0.0.5"
    assert params["port"] == "3389"
    assert params["username"] == "admin"
    assert params["password"] == "s3cret"
    assert params["color-depth"] == "16"
    assert params["ignore-cert"] == "true"  # RDP 既定


def test_password_is_encrypted():
    from app.models import RemoteConnection
    from app.remote_desktop import service

    conn = RemoteConnection(name="x", protocol="vnc", host="h", port=5900)
    service.set_secret_params(conn, {"password": "topsecret"})
    assert "topsecret" not in (conn.secret_params_encrypted or "")
    assert service.build_guacd_params(conn)["password"] == "topsecret"
    # to_out は実際のパスワード値を含めない（has_password フラグのみ）
    out = service.to_out(conn)
    assert "topsecret" not in json.dumps(out)
    assert out["has_password"] is True


def test_handshake_with_mock_guacd():
    """ローカルにモック guacd を立て、select→args→connect の流れを検証する。"""
    from app.remote_desktop.guacd import InstructionParser, encode_instruction, perform_handshake

    async def scenario():
        received: list[list[str]] = []

        async def handle(reader, writer):
            parser = InstructionParser()
            # select を待つ
            while True:
                data = await reader.read(1024)
                if not data:
                    return
                instrs = parser.feed(data.decode())
                received.extend(instrs)
                if any(i[0] == "select" for i in instrs):
                    break
            # args を返す
            writer.write(encode_instruction("args", "VERSION_1_1_0", "hostname", "port", "password"))
            await writer.drain()
            # connect を待つ
            while True:
                data = await reader.read(1024)
                if not data:
                    break
                instrs = parser.feed(data.decode())
                received.extend(instrs)
                if any(i[0] == "connect" for i in instrs):
                    break
            writer.close()

        server = await asyncio.start_server(handle, "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]
        async with server:
            reader, writer = await asyncio.open_connection("127.0.0.1", port)
            await perform_handshake(
                reader, writer, "rdp",
                {"hostname": "10.0.0.5", "port": "3389", "password": "pw"},
                1280, 720,
            )
            await asyncio.sleep(0.1)
            writer.close()
        return received

    received = asyncio.run(scenario())
    kinds = {i[0] for i in received}
    assert "select" in kinds and "connect" in kinds
    select = next(i for i in received if i[0] == "select")
    assert select[1] == "rdp"
    connect = next(i for i in received if i[0] == "connect")
    # connect は args の順: [VERSION, hostname, port, password]
    assert connect[1] == "VERSION_1_1_0"
    assert connect[2] == "10.0.0.5"
    assert connect[3] == "3389"
    assert connect[4] == "pw"


def test_connection_crud_api(admin_client):
    r = admin_client.post(
        "/api/v1/remote/connections",
        json={"name": "test-rdp", "protocol": "rdp", "host": "192.168.1.10", "username": "admin", "password": "pw123"},
        headers=CSRF_HEADERS,
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["port"] == 3389  # 既定ポート
    assert body["has_password"] is True
    assert "pw123" not in json.dumps(body)
    cid = body["id"]

    assert any(c["id"] == cid for c in admin_client.get("/api/v1/remote/connections").json())
    r = admin_client.get("/api/v1/remote/status")
    assert "guacd_available" in r.json()
    assert "self_connection_configured" in r.json()
    assert "self_connection_available" in r.json()
    assert "recovery_hint" in r.json()
    assert admin_client.delete(f"/api/v1/remote/connections/{cid}", headers=CSRF_HEADERS).status_code == 200


def test_endpoint_available_reports_open_and_closed_tcp_ports():
    import socket

    from app.remote_desktop.service import endpoint_available

    server = socket.socket()
    server.bind(("127.0.0.1", 0))
    server.listen(1)
    port = server.getsockname()[1]
    try:
        assert endpoint_available("127.0.0.1", port)
    finally:
        server.close()
    assert not endpoint_available("127.0.0.1", port)


def test_remote_requires_permission(client):
    client.cookies.clear()
    assert client.get("/api/v1/remote/connections").status_code == 401
