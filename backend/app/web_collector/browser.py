"""Web 収集のブラウザ（W2）。**この PC の Google Chrome だけを使い、別のブラウザを取りに行かない**
（2026-11-21 利用者「Chrome を Playwright などで使っているので二重になっていないか」。`playwright install`・起動時の自動導入はしない）。

安全の本体は「**ブラウザの全通信を ControlDeck の中継（ループバックの HTTP プロキシ）に通す**」こと（docs/research-web-collector.md §4）:

- Chrome は `--proxy-server`（利用者名・パスワード付き）と `--proxy-bypass-list=<-loopback>`（localhost・127.0.0.1 も中継へ）で起動。
  `--host-resolver-rules="MAP * ~NOTFOUND , EXCLUDE 127.0.0.1"` で、中継を迂回した名前解決は失敗させる（二重の備え）。
- 中継は CONNECT（https・WebSocket）と http の絶対 URI を扱い、**どちらも `egress.open_guarded`（解決 → 全アドレス検査 → その IP に接続）**。
  iframe・worker・fetch・リダイレクト・WebSocket・IPv6・内部 IP・DNS rebinding は、すべてこの 1 か所で検査される。拒否は 403 で返し記録する。
- WebRTC（`disable_non_proxied_udp`＋ API の除去）・QUIC 無効・Service Worker は block・拡張・同期・バックグラウンド通信は無効。
- 使い捨てのプロファイル（利用者の Cookie・ログイン状態・履歴は使わない）。画像・動画・フォントは取らない。
- 時間（全体）・容量（中継を通る合計バイト・同時接続）・メモリ（Chrome 全プロセスの RSS）に上限。超えたら止める。
- UA は Chrome の本来のまま（詐称しない）。CAPTCHA・ログイン・403/429・robots 拒否は回避せず、状態で返す。
"""
from __future__ import annotations

import asyncio
import base64
import contextlib
import hmac
import logging
import os
import re
import secrets
import shutil
import signal
import subprocess
import tempfile
import time
import weakref
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

from app.security.offline import ensure_external_allowed
from app.web_collector import egress

logger = logging.getLogger("control_deck.web_collector.browser")

NAMES = ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser")
SESSION_TIMEOUT_S = 45
PROXY_MAX_BYTES = 80 * 1024 * 1024
PROXY_MAX_TUNNELS = 64
PROXY_IDLE_S = 30
MAX_RSS_MB = 3072
MAX_HTML_CHARS = 8_000_000
BLOCKED_RESOURCES = frozenset({"image", "media", "font"})

CHROME_ARGS = (
    "--force-webrtc-ip-handling-policy=disable_non_proxied_udp", "--webrtc-ip-handling-policy=disable_non_proxied_udp",
    "--disable-quic", "--disable-background-networking", "--disable-component-update", "--disable-sync",
    "--disable-extensions", "--disable-default-apps", "--disable-domain-reliability", "--no-pings",
    "--no-first-run", "--no-default-browser-check", "--no-service-autorun", "--password-store=basic",
    "--use-mock-keychain", "--disable-breakpad", "--disable-notifications", "--mute-audio",
    "--disable-features=Translate,MediaRouter,OptimizationHints,NetworkPrediction,PrivateNetworkAccessSendPreflights",
    "--blink-settings=imagesEnabled=false",
    "--host-resolver-rules=MAP * ~NOTFOUND , EXCLUDE 127.0.0.1",
)
# WebRTC は中継を通らない UDP を使えるので、API ごと消す（オプションの方針と二重）
_INIT_SCRIPT = """
for (const n of ['RTCPeerConnection','webkitRTCPeerConnection','RTCDataChannel','RTCSessionDescription']) {
  try { Object.defineProperty(window, n, {value: undefined, configurable: false}); } catch (e) {}
}
"""


def system_chrome() -> str:
    for name in NAMES:
        found = shutil.which(name)
        if found:
            return found
    return ""


def chrome_version() -> str:
    chrome = system_chrome()
    if not chrome:
        return ""
    try:
        out = subprocess.run([chrome, "--version"], capture_output=True, text=True, timeout=10, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return out.stdout.strip()[:80]


def is_enabled() -> bool:
    """機能「Web 収集のブラウザ」が使えるか（この PC の Chrome がある）。自動の切り替えの前提。"""
    try:
        from app.features import registry

        return registry.is_enabled("web-browser")
    except Exception:  # noqa: BLE001 - 状態が読めなければ使わない
        return False


class BrowserError(Exception):
    """ブラウザを起動・操作できなかった（安全の拒否ではなく、環境や時間の問題）。"""


# ---- 中継プロキシ ----

@dataclass
class ProxyEvent:
    kind: str          # tunnel / http / rejected / limit
    host: str
    port: int
    reason: str = ""


class RelayProxy:
    """ループバックの HTTP プロキシ。CONNECT と http 絶対 URI を、検査済みの IP へ中継する。認証付き。"""

    def __init__(self, policy: egress.Policy | None = None, *, max_bytes: int = PROXY_MAX_BYTES,
                 max_tunnels: int = PROXY_MAX_TUNNELS, idle_s: float = PROXY_IDLE_S) -> None:
        self.policy = policy or egress.DEFAULT_POLICY
        self.max_bytes = max_bytes
        self.max_tunnels = max_tunnels
        self.idle_s = idle_s
        self.username = "cd-" + secrets.token_hex(6)
        self.password = secrets.token_urlsafe(24)
        self.port = 0
        self.bytes = 0
        self.active = 0
        self.exceeded = False
        self.events: list[ProxyEvent] = []
        self._server: asyncio.AbstractServer | None = None
        self._writers: set[asyncio.StreamWriter] = set()
        self._expected = "Basic " + base64.b64encode(f"{self.username}:{self.password}".encode()).decode()

    # 記録
    def _note(self, kind: str, host: str, port: int, reason: str = "") -> None:
        if len(self.events) < 500:
            self.events.append(ProxyEvent(kind, host, port, reason))

    @property
    def rejected(self) -> list[ProxyEvent]:
        return [e for e in self.events if e.kind == "rejected"]

    async def start(self) -> int:
        self._server = await asyncio.start_server(self._client, "127.0.0.1", 0, limit=64 * 1024)
        self.port = self._server.sockets[0].getsockname()[1]
        return self.port

    async def stop(self) -> None:
        if self._server is not None:
            self._server.close()
        for writer in list(self._writers):
            writer.close()
        if self._server is not None:
            with contextlib.suppress(Exception):
                await asyncio.wait_for(self._server.wait_closed(), 3)
            self._server = None

    # 接続
    async def _client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        self._writers.add(writer)
        try:
            while True:
                try:
                    head = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), 15)
                except (asyncio.IncompleteReadError, asyncio.LimitOverrunError, asyncio.TimeoutError, ConnectionError):
                    return
                lines = head[:-4].decode("latin-1").split("\r\n")
                try:
                    method, target, version = lines[0].split(" ", 2)
                except ValueError:
                    await self._reply(writer, 400, "bad request")
                    return
                headers = [(k.strip(), v.strip()) for k, v in (line.split(":", 1) for line in lines[1:] if ":" in line)]
                auth = next((v for k, v in headers if k.casefold() == "proxy-authorization"), "")
                if not hmac.compare_digest(auth.encode(), self._expected.encode()):
                    await self._reply(writer, 407, "proxy authentication required",
                                      extra=['Proxy-Authenticate: Basic realm="ControlDeck"'])
                    continue  # 同じ接続で認証をやり直せる
                if self.exceeded:
                    self._note("limit", "", 0, "転送量の上限")
                    await self._reply(writer, 503, "size limit")
                    return
                if method.upper() == "CONNECT":
                    await self._connect(target, reader, writer)
                else:
                    await self._forward(method, target, version, headers, reader, writer)
                return
        except Exception as exc:  # noqa: BLE001 - 中継の失敗でサーバーを止めない
            logger.debug("relay error: %s", exc)
        finally:
            self._writers.discard(writer)
            with contextlib.suppress(Exception):
                writer.close()

    @staticmethod
    async def _reply(writer: asyncio.StreamWriter, code: int, text: str, extra: list[str] | None = None) -> None:
        reasons = {400: "Bad Request", 403: "Forbidden", 407: "Proxy Authentication Required", 502: "Bad Gateway", 503: "Service Unavailable"}
        body = text.encode()
        head = [f"HTTP/1.1 {code} {reasons.get(code, 'Error')}", f"Content-Length: {len(body)}", "Content-Type: text/plain; charset=utf-8",
                *(extra or []), "Connection: keep-alive" if code == 407 else "Connection: close"]
        writer.write(("\r\n".join(head) + "\r\n\r\n").encode() + body)
        with contextlib.suppress(ConnectionError):
            await writer.drain()

    async def _open(self, host: str, port: int, writer: asyncio.StreamWriter, kind: str):
        if self.active >= self.max_tunnels:
            self._note("limit", host, port, "同時接続の上限")
            await self._reply(writer, 503, "too many connections")
            return None
        try:
            return await egress.open_guarded(host, port, self.policy)
        except egress.EgressRejected as exc:
            self._note("rejected", host, port, str(exc))
            await self._reply(writer, 403, f"ControlDeck: {exc}")
        except Exception as exc:  # noqa: BLE001 - オフライン調査の停止など
            self._note("rejected", host, port, f"{type(exc).__name__}: {exc}"[:200])
            await self._reply(writer, 403, f"ControlDeck: {exc}")
        return None

    async def _connect(self, target: str, reader, writer) -> None:
        host, _, port_text = target.rpartition(":")
        host = host.strip("[]")
        if not host or not port_text.isdigit():
            await self._reply(writer, 400, "bad CONNECT target")
            return
        port = int(port_text)
        upstream = await self._open(host, port, writer, "tunnel")
        if upstream is None:
            return
        self._note("tunnel", host, port)
        writer.write(b"HTTP/1.1 200 Connection Established\r\n\r\n")
        await writer.drain()
        await self._pipe(reader, writer, *upstream)

    async def _forward(self, method: str, target: str, version: str, headers, reader, writer) -> None:
        parts = urlsplit(target)
        if parts.scheme != "http" or not parts.hostname:
            await self._reply(writer, 400, "absolute http URL required")
            return
        port = parts.port or 80
        upstream = await self._open(parts.hostname, port, writer, "http")
        if upstream is None:
            return
        self._note("http", parts.hostname, port)
        path = (parts.path or "/") + (f"?{parts.query}" if parts.query else "")
        drop = {"proxy-authorization", "proxy-connection", "connection", "keep-alive"}
        lines = [f"{method} {path} {version}", *(f"{k}: {v}" for k, v in headers if k.casefold() not in drop), "Connection: close"]
        up_reader, up_writer = upstream
        up_writer.write(("\r\n".join(lines) + "\r\n\r\n").encode("latin-1"))
        await up_writer.drain()
        await self._pipe(reader, writer, up_reader, up_writer)

    async def _pipe(self, reader, writer, up_reader, up_writer) -> None:
        self.active += 1

        async def copy(src: asyncio.StreamReader, dst: asyncio.StreamWriter) -> None:
            try:
                while True:
                    data = await asyncio.wait_for(src.read(65536), self.idle_s)
                    if not data:
                        break
                    self.bytes += len(data)
                    if self.bytes > self.max_bytes:
                        self.exceeded = True
                        self._note("limit", "", 0, "転送量の上限")
                        break
                    dst.write(data)
                    await dst.drain()
            except (asyncio.TimeoutError, ConnectionError, OSError):
                pass

        tasks = [asyncio.ensure_future(copy(reader, up_writer)), asyncio.ensure_future(copy(up_reader, writer))]
        try:
            await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        finally:
            for task in tasks:
                task.cancel()
            self.active -= 1
            for w in (up_writer, writer):
                with contextlib.suppress(Exception):
                    w.close()


# ---- メモリの測定・後始末 ----

def _chrome_pids(profile_dir: str) -> list[int]:
    """この使い捨てプロファイルで動いている Chrome の全プロセス。"""
    pids = []
    needle = profile_dir.encode()
    for entry in os.listdir("/proc"):
        if not entry.isdigit():
            continue
        try:
            with open(f"/proc/{entry}/cmdline", "rb") as fh:
                if needle in fh.read():
                    pids.append(int(entry))
        except OSError:
            continue
    return pids


def _rss_mb(pids: list[int]) -> float:
    total = 0
    for pid in pids:
        try:
            with open(f"/proc/{pid}/statm") as fh:
                total += int(fh.read().split()[1]) * os.sysconf("SC_PAGE_SIZE")
        except (OSError, ValueError, IndexError):
            continue
    return total / 1048576


_gates: "weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, asyncio.Semaphore]" = weakref.WeakKeyDictionary()


def _gate() -> asyncio.Semaphore:
    """ブラウザの同時起動は 1 つ（RAM を守る）。ループごとに持つ。"""
    loop = asyncio.get_running_loop()
    if loop not in _gates:
        _gates[loop] = asyncio.Semaphore(1)
    return _gates[loop]


@dataclass
class Session:
    page: object
    context: object
    proxy: RelayProxy
    started: float
    peak_rss_mb: float = 0.0
    killed: str = ""
    profile_dir: str = ""
    notes: list[str] = field(default_factory=list)

    def stats(self) -> dict:
        return {"elapsed_s": round(time.monotonic() - self.started, 2), "peak_rss_mb": round(self.peak_rss_mb, 1),
                "proxy_bytes": self.proxy.bytes, "tunnels": sum(1 for e in self.proxy.events if e.kind in ("tunnel", "http")),
                "rejected": [{"host": e.host, "port": e.port, "reason": e.reason} for e in self.proxy.rejected][:20]}


@asynccontextmanager
async def session(*, policy: egress.Policy | None = None, timeout_s: float = SESSION_TIMEOUT_S, block_resources: bool = True,
                  max_rss_mb: int = MAX_RSS_MB, max_bytes: int = PROXY_MAX_BYTES):
    """検査つきの Chrome を 1 つ起こして `Session`（page・proxy・測定）を渡す。出るときに Chrome と一時プロファイルを必ず片付ける。"""
    ensure_external_allowed("ブラウザでの取得")  # 秘密の会話は、起動の前に止める
    chrome = system_chrome()
    if not chrome:
        raise BrowserError("この PC に Google Chrome（または Chromium）が見つかりません。Chrome を入れてください")
    try:
        from playwright.async_api import async_playwright
    except ImportError as exc:
        raise BrowserError("Playwright（Python）が入っていません。ブラウザの操作には本体の環境に playwright が要ります") from exc

    async with _gate():
        proxy = RelayProxy(policy, max_bytes=max_bytes)
        profile = tempfile.mkdtemp(prefix="cd-webbrowser-")
        os.chmod(profile, 0o700)
        port = await proxy.start()
        started = time.monotonic()
        state: dict = {"killed": ""}
        pw_cm = async_playwright()
        playwright = await pw_cm.__aenter__()
        context = None
        watchdog = monitor = None
        try:
            try:
                context = await playwright.chromium.launch_persistent_context(
                    profile, executable_path=chrome, headless=True, args=list(CHROME_ARGS), timeout=20_000,
                    proxy={"server": f"http://127.0.0.1:{port}", "username": proxy.username, "password": proxy.password,
                           "bypass": "<-loopback>"},
                    service_workers="block", accept_downloads=False, bypass_csp=False, ignore_https_errors=False,
                    java_script_enabled=True, permissions=[], locale="ja-JP", chromium_sandbox=True,
                    ignore_default_args=["--hide-scrollbars"])
            except Exception as exc:  # noqa: BLE001
                raise BrowserError(f"Chrome を起動できません: {type(exc).__name__}: {exc}"[:300]) from exc
            sess = Session(page=None, context=context, proxy=proxy, started=started, profile_dir=profile)
            await context.add_init_script(_INIT_SCRIPT)

            async def route(r):
                req = r.request
                scheme = urlsplit(req.url).scheme
                if scheme not in ("http", "https", "about", "blob", "data") or (block_resources and req.resource_type in BLOCKED_RESOURCES):
                    await r.abort()
                else:
                    await r.continue_()

            await context.route("**/*", route)
            sess.page = context.pages[0] if context.pages else await context.new_page()

            async def kill(reason: str) -> None:
                state["killed"] = sess.killed = reason
                for pid in _chrome_pids(profile):
                    with contextlib.suppress(OSError):
                        os.kill(pid, signal.SIGKILL)

            async def watch() -> None:
                await asyncio.sleep(timeout_s)
                await kill(f"{timeout_s:g} 秒の時間切れ")

            async def measure() -> None:
                while True:
                    rss = _rss_mb(_chrome_pids(profile))
                    sess.peak_rss_mb = max(sess.peak_rss_mb, rss)
                    if rss > max_rss_mb:
                        await kill(f"メモリが {max_rss_mb} MB を超えました")
                        return
                    if proxy.exceeded:
                        await kill("転送量の上限")
                        return
                    await asyncio.sleep(0.25)

            watchdog, monitor = asyncio.create_task(watch()), asyncio.create_task(measure())
            yield sess
        finally:
            for task in (watchdog, monitor):
                if task is not None:
                    task.cancel()
            if context is not None:
                with contextlib.suppress(Exception):
                    await asyncio.wait_for(context.close(), 8)
            with contextlib.suppress(Exception):
                await asyncio.wait_for(pw_cm.__aexit__(None, None, None), 8)
            for pid in _chrome_pids(profile):  # 残りがあれば必ず止める
                with contextlib.suppress(OSError):
                    os.kill(pid, signal.SIGKILL)
            await proxy.stop()
            shutil.rmtree(profile, ignore_errors=True)


# ---- 1 ページの描画 ----

_CAPTCHA = re.compile(r"captcha|cf-chl-|challenge-platform|are you a robot|ロボットではありません|hcaptcha|recaptcha|prove your humanity|"
                      r"verify you are (?:a )?human|just a moment", re.I)


async def _settle(page, deadline: float) -> None:
    """読み込みと描画が落ち着くまで待つ（通信が静まる → 本文の長さが 2 回続けて同じ）。"""
    with contextlib.suppress(Exception):
        await page.wait_for_load_state("networkidle", timeout=max(1000, min(8000, int((deadline - time.monotonic()) * 1000))))
    last = -1
    for _ in range(6):
        if time.monotonic() > deadline:
            break
        try:
            size = await page.evaluate("() => (document.body ? document.body.innerText.length : 0)")
        except Exception:  # noqa: BLE001
            break
        if size == last and size > 0:
            break
        last = size
        await asyncio.sleep(0.4)


async def render(url: str, *, policy: egress.Policy | None = None, timeout_s: float = 30, **session_opts) -> dict:
    """URL を Chrome（検査つきの中継経由）で開き、描画後の HTML を返す。

    返す dict: status（success / blocked / captcha / login_required / security_rejected / timeout / http_error / empty）、
    html, final_url, title, http_status, error, stats（時間・RAM・転送量・止めた通信）。
    環境の問題（Chrome が無い・起動できない）は BrowserError。"""
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        return {"status": "security_rejected", "html": "", "final_url": url, "title": "", "http_status": 0,
                "error": "http(s) の URL だけを開きます", "stats": {}}
    timeout_s = min(max(timeout_s, 5), SESSION_TIMEOUT_S)
    out = {"status": "success", "html": "", "final_url": url, "title": "", "http_status": 0, "error": "", "stats": {}}
    async with session(policy=policy, timeout_s=timeout_s + 8, **session_opts) as sess:
        page = sess.page
        deadline = time.monotonic() + timeout_s
        try:
            response = await page.goto(url, timeout=int(timeout_s * 1000), wait_until="domcontentloaded")
            out["http_status"] = response.status if response else 0
            await _settle(page, deadline)
            html = (await page.content())[:MAX_HTML_CHARS]
            out["html"], out["final_url"], out["title"] = html, page.url, await page.title()
            has_password = await page.locator("input[type=password]").count() > 0
            visible = await page.evaluate("() => (document.body ? document.body.innerText.length : 0)")
        except Exception as exc:  # noqa: BLE001 - 中継の拒否・時間切れ・Chrome の停止
            rejected = sess.proxy.rejected
            if rejected:
                out.update(status="security_rejected", error=f"{rejected[0].reason}（{rejected[0].host}:{rejected[0].port}）")
            elif sess.killed:
                out.update(status="timeout" if "時間" in sess.killed else "too_large", error=sess.killed)
            elif type(exc).__name__ == "TimeoutError":
                out.update(status="timeout", error=f"{timeout_s:g} 秒で開けません")
            else:
                out.update(status="http_error", error=f"{type(exc).__name__}: {exc}"[:300])
            out["stats"] = sess.stats()
            return out
        code = out["http_status"]
        final_host = (urlsplit(out["final_url"]).hostname or "").strip("[]")
        denied = next((e for e in sess.proxy.rejected if e.host == final_host), None)
        if denied is not None:  # 開こうとした先（リダイレクトの先を含む）を中継が断った。サイトの 403 ではない
            out.update(status="security_rejected", error=f"{denied.reason}（{denied.host}:{denied.port}）")
        elif code in (401, 403, 429) or code >= 500 and _CAPTCHA.search(html[:20000]):
            out.update(status="captcha" if _CAPTCHA.search(html[:20000]) else "blocked", error=f"HTTP {code}「{out['title'][:60]}」（回避しません）")
        elif code >= 400:
            out.update(status="http_error", error=f"HTTP {code}")
        elif _CAPTCHA.search(html[:30000]) and visible < 1500:
            out.update(status="captcha", error="ロボット確認のページ（回避しません）")
        elif has_password and visible < 2000:
            out.update(status="login_required", error="ログインが要るページです（ログインはしません）")
        elif sess.proxy.rejected:
            out["warnings"] = [f"内部・許可外への通信 {len(sess.proxy.rejected)} 件を止めました"]
        out["stats"] = sess.stats()
    return out
