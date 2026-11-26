"""W3b-1: 内部用の同一 origin HTTP 巡回境界。公開 API/Jobs には未接続。

1 セッションに 1 origin、直列、全時間/容量/要求数の上限。robots を確認できなければ拒否。
robots とページの全 redirect は同一 origin に限定し、既存 egress を必ず通す。
取得済み bytes は内部処理のみ。共有キャッシュや原文配信は行わない。
"""
from __future__ import annotations

import asyncio
import json
import math
import time
from pathlib import Path
from dataclasses import dataclass
from typing import Awaitable, Callable

import httpx
from app.security.offline import ensure_external_allowed, ExternalResearchBlocked
from app.web_collector import crawl, egress

AGENT = "ControlDeck-WebCollector"
MAX_BODY = 20 * 1024 * 1024
MAX_ROBOTS = 500_000
MAX_TOTAL = 250 * 1024 * 1024
MAX_REQUESTS = 400
MAX_SECONDS = 600
MAX_REDIRECTS = 5
WORKER = Path(__file__).with_name("crawl_robots_worker.py")


class StopCrawl(Exception):
    def __init__(self, status: str):
        super().__init__(status)
        self.status = status


@dataclass(frozen=True)
class Response:
    url: str
    status: int
    content_type: str
    body: bytes
    retry_after: str = ""


async def parse_robots(text: str, url: str) -> tuple[bool, float]:
    from app.features import registry
    python = await asyncio.to_thread(lambda: registry._feature_root("web-robots") / "venv" / "bin" / "python")
    if not await asyncio.to_thread(python.is_file):
        raise StopCrawl("robots_denied")
    proc = await asyncio.create_subprocess_exec(
        str(python), "-I", str(WORKER), stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
        env={"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8"})
    try:
        raw, _ = await asyncio.wait_for(proc.communicate(json.dumps({"text": text, "url": url}).encode()), 4)
        if proc.returncode != 0 or len(raw) > 1024:
            raise StopCrawl("robots_denied")
        result = json.loads(raw)
        if type(result["allowed"]) is not bool:
            raise ValueError("invalid robots verdict")
        delay = float(result["delay"])
        if not math.isfinite(delay) or delay < 0:
            raise ValueError("invalid crawl-delay")
        return result["allowed"], delay
    except (OSError, ValueError, KeyError, TypeError, TimeoutError) as exc:
        raise StopCrawl("robots_denied") from exc
    finally:
        if proc.returncode is None:
            proc.kill()
            await proc.wait()


class CrawlTransport:
    def __init__(self, root: str, *, policy: egress.Policy | None = None,
                 parser: Callable[[str, str], Awaitable[tuple[bool, float]]] = parse_robots):
        self.root, _ = crawl._url(root)
        self.policy = policy or egress.DEFAULT_POLICY
        self.parser = parser
        self.deadline = time.monotonic() + MAX_SECONDS
        self.bytes = 0
        self.requests = 0
        self.delay = 1.0
        self.last = 0.0
        self.stopped = False
        self._robots: str | None = None
        self._lock = asyncio.Lock()

    def _target(self, parent: str, url: str) -> str:
        target = crawl.scoped_url(self.root, parent, url)
        if target is None:
            raise StopCrawl("security_rejected")
        return target

    def _remaining(self) -> float:
        remaining = self.deadline - time.monotonic()
        if remaining <= 0 or self.requests >= MAX_REQUESTS or self.stopped:
            raise StopCrawl("budget_exhausted" if not self.stopped else "blocked")
        return remaining

    async def _request(self, client: httpx.AsyncClient, url: str, cap: int) -> Response:
        ensure_external_allowed("Web 巡回")
        wait = max(0.0, self.last + self.delay - time.monotonic())
        if wait >= self._remaining():
            raise StopCrawl("budget_exhausted")
        await asyncio.sleep(wait)
        self.requests += 1
        self.last = time.monotonic()
        async with client.stream("GET", url) as r:
            if r.status_code in (429, 503) or r.headers.get("retry-after"):
                # 永続的な再開時刻を管理する実行側が入るまでは、待って再試行せず止める。
                self.stopped = True
                raise StopCrawl("blocked")
            if r.status_code in (301, 302, 303, 307, 308):
                return Response(url, r.status_code, r.headers.get("location", ""), b"")
            if r.status_code != 200:
                return Response(url, r.status_code, "", b"")
            if r.headers.get("content-encoding", "identity").casefold() != "identity":
                raise StopCrawl("unsupported")
            limit = min(cap, MAX_TOTAL - self.bytes)
            if limit <= 0:
                raise StopCrawl("budget_exhausted")
            raw_length = r.headers.get("content-length")
            if raw_length is not None and (not raw_length.isdecimal() or int(raw_length) > limit):
                raise StopCrawl("too_large")
            data = bytearray()
            async for chunk in r.aiter_bytes(chunk_size=64 * 1024):
                self.bytes += len(chunk)
                if len(data) + len(chunk) > limit or self.bytes > MAX_TOTAL:
                    raise StopCrawl("too_large")
                data.extend(chunk)
            return Response(url, 200, r.headers.get("content-type", ""), bytes(data))

    async def _load_robots(self, client: httpx.AsyncClient) -> str:
        target = self._target(self.root, "/robots.txt")
        for _ in range(MAX_REDIRECTS + 1):
            r = await self._request(client, target, MAX_ROBOTS)
            if r.status in (301, 302, 303, 307, 308):
                target = self._target(target, r.content_type)
                continue
            if r.status in (404, 410):
                return ""
            if r.status == 200:
                return r.body.decode("utf-8-sig", errors="strict")
            break
        raise StopCrawl("robots_denied")

    async def get(self, url: str) -> Response:
        """1 URL の内部取得。停止・拒否を呼び出し側へ返す。自動再試行なし。"""
        ensure_external_allowed("Web 巡回")
        target = self._target(self.root, url)
        async with self._lock:
            try:
                async with asyncio.timeout(min(self._remaining(), 30)):
                    async with egress.guarded_client(timeout=15, policy=self.policy,
                                                     headers={"User-Agent": AGENT, "Accept-Encoding": "identity"}) as client:
                        if self._robots is None:
                            self._robots = await self._load_robots(client)
                        for _ in range(MAX_REDIRECTS + 1):
                            allowed, delay = await self.parser(self._robots, target)
                            if not allowed or not math.isfinite(delay) or delay < 0:
                                raise StopCrawl("robots_denied")
                            self.delay = max(self.delay, delay)
                            response = await self._request(client, target, MAX_BODY)
                            if response.status in (301, 302, 303, 307, 308):
                                target = self._target(target, response.content_type)
                                continue
                            if response.status in (401, 403):
                                self.stopped = True
                                raise StopCrawl("blocked")
                            if response.status == 200 and "html" in response.content_type.casefold() and len(response.body) < 60_000:
                                from app.web_collector import fetch
                                if fetch._CAPTCHA.search(fetch._decode(response.body[:20_000], response.content_type)):
                                    self.stopped = True
                                    raise StopCrawl("captcha")
                            return response
                        raise StopCrawl("http_error")
            except (egress.EgressRejected, ExternalResearchBlocked) as exc:
                raise StopCrawl("security_rejected") from exc
            except UnicodeError as exc:
                raise StopCrawl("robots_denied") from exc
            except (TimeoutError, httpx.TimeoutException) as exc:
                raise StopCrawl("timeout") from exc
            except (httpx.HTTPError, OSError) as exc:
                raise StopCrawl("http_error") from exc
