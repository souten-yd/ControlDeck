"""QNAP ニュースハブを LAN で自動で見つける（設定 →「探す」ボタン）。

2026-11-25 利用者「公開されている場合自動でアドレスを検知できないか？」（ハブの URL にポートを付けずに入れて「ハブにつながりません（ConnectError）」になった）。
相手の仕様は QnapNewsHub の docs/DESIGN.md 3.5（UDP の発見の応答）。

- ① この PC の `http://127.0.0.1:8787/api/v1/health` を試す。② UDP の発見の問い `{"q":"qnapnewshub-discover","v":1}` を、各インターフェースのブロードキャストのアドレスと
  255.255.255.255 の UDP 8788 へ 1 回ずつ送り、1.5 秒のあいだ答えを集める。③ 見つかったハブを（名前・版・URL・指紋で）返す。
- **答えを信じすぎない**: 答えの URL は、**その答えが来た送り元の IP と同じホストのものだけ**採る（別のホストへ誘導する答え・なりすまし・SSRF の誘導を捨てる）。
  形の検査（大きさ・魔法の文字列・版・各項目の型と長さ・URL は http/https・利用者情報・パス・クエリ無し）を通らない答えは捨てる。1 回の探しで 32 件まで。
- 発見は**情報を受け取るだけ**。つなぐのは従来どおり、ハブの番号（6 桁）と指紋の見比べが要る（hub_link.pair / confirm。変えない）。この関数はトークン・鍵・状態に触れない。
- 同期の関数（ソケットを待つ）。async からは呼ばず、endpoint を `def`（threadpool）にして呼ぶ（AGENTS.md の絶対ルール 9）。
"""
from __future__ import annotations

import fcntl
import ipaddress
import json
import logging
import re
import socket
import struct
import time
from urllib.parse import urlsplit

import httpx

logger = logging.getLogger("control_deck.news.hub_discovery")

DISCOVERY_PORT = 8788
HUB_PORT = 8787
MAGIC_Q = "qnapnewshub-discover"
MAGIC_A = "qnapnewshub-here"
PROTOCOL = 1
WAIT_SECONDS = 1.5
MAX_REPLY = 2048
MAX_FOUND = 32
LOCAL_URL = f"http://127.0.0.1:{HUB_PORT}"
_FP = re.compile(r"^[0-9a-f]{4}(-[0-9a-f]{4}){4}$")
_SIOCGIFBRDADDR = 0x8919


def broadcast_addresses() -> list[str]:
    """各インターフェースのブロードキャストのアドレス（Linux の ioctl。取れなければ 255.255.255.255 だけ）。"""
    found: list[str] = []
    try:
        names = [name for _, name in socket.if_nameindex()]
    except OSError:
        names = []
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        for name in names:
            try:
                packed = fcntl.ioctl(sock.fileno(), _SIOCGIFBRDADDR, struct.pack("256s", name[:15].encode()))
            except OSError:
                continue
            addr = socket.inet_ntoa(packed[20:24])
            if addr not in ("0.0.0.0", "255.255.255.255") and addr not in found:
                found.append(addr)
    found.append("255.255.255.255")
    return found


def parse_reply(data: bytes, source_ip: str) -> dict | None:
    """答えを検査して、採れる形（name・version・url・fingerprint）にする。形が違う・URL が送り元と一致しないなら None。"""
    if not 0 < len(data) <= MAX_REPLY:
        return None
    try:
        body = json.loads(data.decode("utf-8"))
        source = ipaddress.ip_address(source_ip)
    except (UnicodeDecodeError, ValueError):
        return None
    if not isinstance(body, dict) or body.get("q") != MAGIC_A or body.get("v") != PROTOCOL:
        return None
    if source.is_multicast or source.is_unspecified:
        return None
    name, version, fingerprint, urls = body.get("name"), body.get("version"), body.get("fingerprint", ""), body.get("urls")
    if not (isinstance(name, str) and 0 < len(name) <= 60 and isinstance(version, str) and len(version) <= 30 and isinstance(fingerprint, str) and isinstance(urls, list)):
        return None
    if fingerprint and not _FP.match(fingerprint):
        return None
    for raw in urls[:12]:
        if not isinstance(raw, str) or len(raw) > 100:
            continue
        try:
            parts = urlsplit(raw)
            port = parts.port
            host = ipaddress.ip_address(parts.hostname or "")
        except ValueError:
            continue
        if parts.scheme not in ("http", "https") or parts.username or parts.password or parts.query or parts.fragment or parts.path not in ("", "/") or not port:
            continue
        if host != source:         # 送り元と別のホストを指す URL は採らない（なりすまし・SSRF の誘導）
            continue
        netloc = f"[{host}]" if host.version == 6 else str(host)
        return {"name": name, "version": version, "url": f"{parts.scheme}://{netloc}:{port}", "fingerprint": fingerprint}
    return None


def _probe_local() -> dict | None:
    """この PC の 127.0.0.1:8787 にハブがいるか（health が status=ok と版を返す）。指紋は health に無いので空。"""
    try:
        with httpx.Client(timeout=1.0, follow_redirects=False, trust_env=False) as client:
            reply = client.get(f"{LOCAL_URL}/api/v1/health")
        body = reply.json()
    except (httpx.HTTPError, ValueError):
        return None
    if reply.status_code == 200 and isinstance(body, dict) and body.get("status") == "ok" and isinstance(body.get("version"), str):
        return {"name": "QNAP News Hub（この PC）", "version": body["version"][:30], "url": LOCAL_URL, "fingerprint": ""}
    return None


def _collect_udp(wait: float, port: int) -> tuple[list[dict], list[str]]:
    query = json.dumps({"q": MAGIC_Q, "v": PROTOCOL}, separators=(",", ":")).encode()
    targets = broadcast_addresses()
    found: dict[str, dict] = {}
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        sock.bind(("", 0))
        for target in targets:
            try:
                sock.sendto(query, (target, port))
            except OSError as exc:
                logger.debug("発見の問いを送れません %s: %s", target, exc)
        deadline = time.monotonic() + wait
        while len(found) < MAX_FOUND:
            left = deadline - time.monotonic()
            if left <= 0:
                break
            sock.settimeout(left)
            try:
                data, addr = sock.recvfrom(MAX_REPLY + 1)
            except (socket.timeout, OSError):
                break
            hub = parse_reply(data, addr[0])
            if hub and addr[0] not in found:
                found[addr[0]] = {**hub, "from": addr[0]}
    finally:
        sock.close()
    return list(found.values()), [f"{t}:{port}（UDP）" for t in targets]


def discover(wait: float = WAIT_SECONDS, port: int = DISCOVERY_PORT, probe_local: bool = True) -> dict:
    """ハブを探す。{"found": [{name, version, url, fingerprint, from}], "tried": [試した場所の文字列]}。見つからなくても例外にしない。"""
    tried: list[str] = []
    found: list[dict] = []
    if probe_local:
        tried.append(LOCAL_URL)
        local = _probe_local()
        if local:
            found.append({**local, "from": "127.0.0.1"})
    try:
        udp, udp_tried = _collect_udp(wait, port)
    except OSError as exc:
        logger.warning("発見の問いを送れませんでした: %s", exc)
        udp, udp_tried = [], []
    tried += udp_tried
    seen = {h["url"] for h in found}
    found += [h for h in udp if h["url"] not in seen]
    return {"found": found, "tried": tried}
