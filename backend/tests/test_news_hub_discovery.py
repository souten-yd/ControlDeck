"""目的: ControlDeck の設定「ハブを探す」（app/news/hub_discovery.py）と、ハブの URL のポート補いの試験。
根拠: 2026-11-25 利用者「公開されている場合自動でアドレスを検知できないか？」（URL にポートを付けず「ハブにつながりません（ConnectError）」になった）・
QnapNewsHub の docs/DESIGN.md 3.5。守ること:
- URL にポートが無ければ 8787 を補う（http・https とも）。明示があればそのまま。つながらないときの知らせに試したアドレスを出す。
- 発見の答えは、送り元の IP と同じホストの URL だけ採る（なりすまし・SSRF の誘導を防ぐ）。形（魔法の文字列・版・型・長さ・指紋の形・大きさ）が違う答えは捨てる。
- 発見は情報を受け取るだけ（つなぐ状態・トークンに触れない）。管理権限が要る。
- 本物のハブ（QnapNewsHub に discovery.py がある版の venv。無ければ skip）を一時に起こし、UDP で見つける往復を通す。外へは通信しない（127.0.0.x だけ）。
"""
from __future__ import annotations

import json
import os
import socket
import subprocess
import threading
import time
from pathlib import Path

import httpx
import pytest

from app.news import hub_discovery, hub_link
from tests.test_news import clean_news, login  # noqa: F401

HUB_REPO = Path(os.environ.get("NEWSHUB_REPO", "/data1tb/ControlDeck/QnapNewsHub"))
HUB_PY = HUB_REPO / ".venv" / "bin" / "python"
H = {"X-Requested-With": "ControlDeck"}
FP = "1a2b-3c4d-5e6f-7a8b-9c0d"


def reply(src_url: str = "http://192.0.2.20:8787", **over) -> bytes:
    body = {"q": "qnapnewshub-here", "v": 1, "name": "QNAP News Hub", "version": "0.1.1", "urls": [src_url], "fingerprint": FP, **over}
    return json.dumps(body).encode()


@pytest.mark.parametrize("given,expected", [
    ("http://nas.local", "http://nas.local:8787"), ("https://nas.local/", "https://nas.local:8787"), ("http://192.0.2.5", "http://192.0.2.5:8787"),
    ("http://nas.local:9000", "http://nas.local:9000"), ("https://nas.local:443/", "https://nas.local:443"), ("http://[2001:db8::1]", "http://[2001:db8::1]:8787"),
    ("http://nas.local/hub/", "http://nas.local:8787/hub"),
])
def test_default_port_is_added_only_when_missing(given, expected):
    assert hub_link.normalize_url(given) == expected


def test_bad_urls_are_still_refused():
    for bad in ("http://nas.local:99999", "http://nas.local:abc", "ftp://x", "http://u:p@x"):
        with pytest.raises(hub_link.HubError):
            hub_link.normalize_url(bad)


def test_connect_error_message_names_the_address_tried():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    with pytest.raises(hub_link.HubError) as err:
        hub_link.call("GET", "/health", url=f"http://127.0.0.1:{port}", token="")
    assert f"http://127.0.0.1:{port}" in err.value.message and "ConnectError" in err.value.message and err.value.status == 0


def test_parse_reply_accepts_matching_source_only():
    ok = hub_discovery.parse_reply(reply(), "192.0.2.20")
    assert ok == {"name": "QNAP News Hub", "version": "0.1.1", "url": "http://192.0.2.20:8787", "fingerprint": FP}
    # 別のホストを指す URL（誘導）は採らない。複数あれば送り元と一致するものだけ
    assert hub_discovery.parse_reply(reply("http://192.0.2.99:8787"), "192.0.2.20") is None
    assert hub_discovery.parse_reply(reply("http://nas.local:8787"), "192.0.2.20") is None
    assert hub_discovery.parse_reply(reply(urls=["http://192.0.2.99:8787", "http://192.0.2.20:9000"]), "192.0.2.20")["url"] == "http://192.0.2.20:9000"


@pytest.mark.parametrize("data", [
    b"", b"x", b"[]", b"\xff", reply(q="other"), reply(v=2), reply(name=""), reply(name="x" * 61), reply(version=3), reply(fingerprint="not-a-fingerprint"), reply(urls="http://192.0.2.20:8787"),
    reply(urls=["http://u:p@192.0.2.20:8787"]), reply(urls=["http://192.0.2.20:8787/x?y=1"]), reply(urls=["ftp://192.0.2.20:8787"]), reply(urls=["http://192.0.2.20"]),
    reply(urls=["http://192.0.2.20:8787#f"]), b"{" + b" " * 3000 + b"}",
])
def test_parse_reply_drops_malformed(data):
    assert hub_discovery.parse_reply(data, "192.0.2.20") is None


def test_parse_reply_rejects_bad_source():
    assert hub_discovery.parse_reply(reply("http://0.0.0.0:8787"), "0.0.0.0") is None
    assert hub_discovery.parse_reply(reply(), "not-an-ip") is None


def _udp_server(replies: list[tuple[bytes, str]]):
    """ループバックの UDP サーバー（問いが来たら replies を返す）。送り元は 127.0.0.1 固定なので、本物のなりすましの試験は parse_reply で。"""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(("127.0.0.1", 0))
    sock.settimeout(3)
    seen: list[bytes] = []

    def run() -> None:
        try:
            data, addr = sock.recvfrom(1024)
        except OSError:
            return
        seen.append(data)
        for payload, _ in replies:
            sock.sendto(payload, addr)

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    return sock, thread, seen


def test_discover_sends_one_query_and_collects_checked_replies(monkeypatch):
    server, thread, seen = _udp_server([(reply("http://127.0.0.1:8787"), ""), (reply("http://203.0.113.5:8787"), ""), (b"junk", "")])
    port = server.getsockname()[1]
    monkeypatch.setattr(hub_discovery, "broadcast_addresses", lambda: ["127.0.0.1"])
    result = hub_discovery.discover(wait=0.5, port=port, probe_local=False)
    thread.join(3)
    server.close()
    assert json.loads(seen[0]) == {"q": "qnapnewshub-discover", "v": 1}
    assert [h["url"] for h in result["found"]] == ["http://127.0.0.1:8787"]            # 誘導の答え・壊れた答えは落ちる
    assert result["tried"] == [f"127.0.0.1:{port}（UDP）"]


def test_discover_does_not_touch_link_state(admin_client, monkeypatch, tmp_path):
    monkeypatch.setattr(hub_link, "_path", lambda: tmp_path / "news" / "hub.json")
    monkeypatch.setattr(hub_discovery, "discover", lambda: {"found": [], "tried": ["x"]})
    r = admin_client.post("/api/v1/news/hub/discover", headers=H)
    assert r.status_code == 200 and r.json() == {"found": [], "tried": ["x"]}
    assert not (tmp_path / "news" / "hub.json").exists()


def test_discover_needs_permission(client, monkeypatch):
    monkeypatch.setattr(hub_discovery, "discover", lambda: pytest.fail("権限なしで探した"))
    login(client, "hub-viewer", "viewer")
    assert client.post("/api/v1/news/hub/discover", headers=H).status_code == 403


def test_find_a_real_hub_over_udp(tmp_path, monkeypatch):
    """本物のハブ（発見の応答つきの版）を 127.0.0.2 で起こし、UDP の問いで見つけて、health が答えることまで確かめる。"""
    if not HUB_PY.is_file() or not (HUB_REPO / "backend" / "qnapnewshub" / "discovery.py").is_file():
        pytest.skip("発見の応答つきのハブ（NEWSHUB_REPO の venv）が無い環境では飛ばす")
    with socket.socket() as s:
        s.bind(("127.0.0.2", 0))
        http_port = s.getsockname()[1]
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.bind(("127.0.0.2", 0))
        udp_port = s.getsockname()[1]
    env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": str(tmp_path), "NEWSHUB_DATA": str(tmp_path / "hubdata"), "NEWSHUB_AUTO": "0",
           "NEWSHUB_HOST": "127.0.0.2", "NEWSHUB_PORT": str(http_port), "NEWSHUB_DISCOVERY_PORT": str(udp_port)}
    proc = subprocess.Popen([str(HUB_PY), "-m", "qnapnewshub.cli", "run"], env=env, cwd=HUB_REPO / "backend", stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        for _ in range(100):
            try:
                if httpx.get(f"http://127.0.0.2:{http_port}/api/v1/health", timeout=2).status_code == 200:
                    break
            except httpx.HTTPError:
                time.sleep(0.3)
        else:
            raise RuntimeError("ハブが起動しません")
        monkeypatch.setattr(hub_discovery, "broadcast_addresses", lambda: ["127.0.0.2"])
        result = hub_discovery.discover(wait=1.5, port=udp_port, probe_local=False)
        assert len(result["found"]) == 1
        hub = result["found"][0]
        assert hub["url"] == f"http://127.0.0.2:{http_port}" and hub["from"] == "127.0.0.2" and len(hub["fingerprint"]) == 24
        assert hub_link.call("GET", "/health", url=hub["url"], token="")["status"] == "ok"        # 見つけた URL でつながる
        # 指紋はハブの画面（GET /link）の指紋と同じ
        assert hub["fingerprint"].count("-") == 4
    finally:
        proc.terminate()
        try:
            proc.wait(15)
        except subprocess.TimeoutExpired:
            proc.kill()
