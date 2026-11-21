"""外部への接続の出口。**名前を解決 → 全アドレスを検査 → 検査した IP にそのまま接続** を 1 か所で行う。

検査の後で HTTP クライアントが別に名前解決すると、DNS rebinding で内部のアドレスへ届く
（CVE-2026-55391 と同型。docs/research-web-collector.md §3）。httpcore のネットワークバックエンドを差し替え、
接続そのものを検査済みの IP に固定する。TLS の SNI と Host は元の名前のまま（httpcore が上の層で付ける）。

HTTP の取得（fetch.py）とブラウザの中継（W2 の browser.py）が同じ `open_guarded` を使う。
"""
from __future__ import annotations

import asyncio
import ipaddress
import socket
from dataclasses import dataclass, field

import httpcore
import httpx

ALLOWED_PORTS = frozenset({80, 443})
_CGNAT = ipaddress.ip_network("100.64.0.0/10")
_BENCHMARK = ipaddress.ip_network("198.18.0.0/15")


class EgressRejected(Exception):
    """安全のため接続しなかった（内部のアドレス・許可されないポート・解決できない名前）。"""


@dataclass(frozen=True)
class Policy:
    ports: frozenset[int] = ALLOWED_PORTS
    # 試験だけで使う: 名前 → 返すアドレス（実際の DNS を引かない）と、公開とみなす範囲（ローカルの試験サーバー）
    resolver_override: dict[str, list[str]] = field(default_factory=dict)
    allow_networks: tuple[str, ...] = ()


DEFAULT_POLICY = Policy()


def is_public(address: str) -> bool:
    """公開のアドレスか。内部・ループバック・リンクローカル（クラウドのメタデータ 169.254.169.254 を含む）・CGNAT などは False。"""
    try:
        ip = ipaddress.ip_address(address.split("%", 1)[0])
    except ValueError:
        return False
    if isinstance(ip, ipaddress.IPv6Address):
        mapped = ip.ipv4_mapped or ip.sixtofour
        if mapped is not None:
            return is_public(str(mapped))
        if ip.teredo:
            return False
    if (ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_multicast or ip.is_reserved
            or ip.is_unspecified or not ip.is_global):
        return False
    if isinstance(ip, ipaddress.IPv4Address) and (ip in _CGNAT or ip in _BENCHMARK):
        return False
    return True


async def resolve_public(host: str, port: int, policy: Policy = DEFAULT_POLICY) -> list[str]:
    """名前を解決し、**すべて**が公開のアドレスなら返す。1 つでも内部なら拒否（混ぜて返す DNS への備え）。"""
    from app.security.offline import ensure_external_allowed

    ensure_external_allowed(f"接続 {host}:{port}")  # オフライン調査（アシスタントの調査の中だけ）。名前解決の前に止める
    if port not in policy.ports:
        raise EgressRejected(f"ポート {port} への接続は許可していません")
    host = host.strip("[]")
    if host in policy.resolver_override:
        addresses = list(policy.resolver_override[host])
    else:
        try:
            ipaddress.ip_address(host)
            addresses = [host]
        except ValueError:
            try:
                infos = await asyncio.get_running_loop().getaddrinfo(host, port, type=socket.SOCK_STREAM)
            except socket.gaierror as exc:
                raise EgressRejected(f"名前を解決できません: {host}") from exc
            addresses = list(dict.fromkeys(info[4][0] for info in infos))
    if not addresses:
        raise EgressRejected(f"名前を解決できません: {host}")
    allowed = [ipaddress.ip_network(n) for n in policy.allow_networks]
    bad = [a for a in addresses if not is_public(a) and not any(ipaddress.ip_address(a) in n for n in allowed)]
    if bad:
        raise EgressRejected(f"内部のアドレスへは接続しません（{host} → {bad[0]}）")
    return addresses


class GuardedBackend(httpcore.AsyncNetworkBackend):
    """httpcore の接続を、検査済みの IP へ固定する。"""

    def __init__(self, policy: Policy = DEFAULT_POLICY) -> None:
        self._inner = httpcore.AnyIOBackend()
        self._policy = policy

    async def connect_tcp(self, host, port, timeout=None, local_address=None, socket_options=None):
        addresses = await resolve_public(host, int(port), self._policy)
        last: Exception | None = None
        for address in addresses:
            try:
                return await self._inner.connect_tcp(address, port, timeout=timeout, local_address=local_address,
                                                     socket_options=socket_options)
            except (httpcore.ConnectError, httpcore.ConnectTimeout, OSError) as exc:
                last = exc
        raise httpcore.ConnectError(str(last) if last else "接続できません")

    async def connect_unix_socket(self, *args, **kwargs):  # noqa: D401 - 使わせない
        raise EgressRejected("UNIX ソケットへは接続しません")

    async def sleep(self, seconds: float) -> None:
        await self._inner.sleep(seconds)


def guarded_transport(policy: Policy = DEFAULT_POLICY) -> httpx.AsyncHTTPTransport:
    transport = httpx.AsyncHTTPTransport(trust_env=False, retries=0)
    transport._pool = httpcore.AsyncConnectionPool(  # noqa: SLF001 - httpx は network_backend を受け取らない
        ssl_context=httpx.create_ssl_context(trust_env=False), max_connections=10, http1=True, http2=False,
        retries=0, network_backend=GuardedBackend(policy))
    return transport


def guarded_client(*, timeout: float = 30, policy: Policy = DEFAULT_POLICY, headers: dict | None = None) -> httpx.AsyncClient:
    """外部の取得に使うクライアント。リダイレクトは自動で追わない（呼び出し側が各段の URL を検査して追う）。
    環境変数のプロキシは使わない（中継で検査を外されないため）。"""
    return httpx.AsyncClient(transport=guarded_transport(policy), timeout=timeout, follow_redirects=False,
                             trust_env=False, headers=headers or {})


async def open_guarded(host: str, port: int, policy: Policy = DEFAULT_POLICY, timeout: float = 15):
    """ブラウザの中継（CONNECT）用: 検査済みの IP への生の TCP 接続（asyncio のストリーム）。"""
    addresses = await resolve_public(host, port, policy)
    last: Exception | None = None
    for address in addresses:
        try:
            return await asyncio.wait_for(asyncio.open_connection(address, port), timeout)
        except (OSError, asyncio.TimeoutError) as exc:
            last = exc
    raise EgressRejected(f"接続できません: {host}:{port}（{last}）")
