"""robots.txt の確認（W2: ブラウザへ進むときだけ。巡回の W3 で Protego に替えてもよい）。

Google の仕様に沿う最小の実装: User-agent のグループ選択（自分の名前 → `*`）、最長一致の規則（同じ長さなら Allow）、
`*` と `$`。robots.txt が 4xx なら制限なし、5xx・通信の失敗は**拒否扱い**（確かめられないものは進まない）。
取得は egress（検査済みの IP）。1 つの origin につき 1 時間覚える。
"""
from __future__ import annotations

import re
import time
from urllib.parse import urlsplit

from app.web_collector import egress

AGENT = "ControlDeck-WebCollector"
TTL_S = 3600
_cache: dict[str, tuple[float, str | None, int]] = {}  # origin → (取得時刻, 本文 None=取得失敗, 状態)


def _pattern(path: str) -> re.Pattern:
    end = path.endswith("$")
    body = re.escape(path[:-1] if end else path).replace(r"\*", ".*")
    return re.compile(body + ("$" if end else ""))


def allowed(text: str, path: str, agent: str = AGENT) -> bool:
    """robots.txt の本文に対して、path（クエリ付き）を取得してよいか。"""
    groups: list[tuple[list[str], list[tuple[bool, str]]]] = []
    agents: list[str] = []
    rules: list[tuple[bool, str]] = []
    reading_agents = False
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].strip()
        if ":" not in line:
            continue
        key, value = (x.strip() for x in line.split(":", 1))
        key = key.casefold()
        if key == "user-agent":
            if not reading_agents and (agents or rules):
                groups.append((agents, rules))
                agents, rules = [], []
            agents.append(value.casefold())
            reading_agents = True
        elif key in ("allow", "disallow"):
            reading_agents = False
            if value:  # 空の Disallow は何も禁じない
                rules.append((key == "allow", value))
    if agents or rules:
        groups.append((agents, rules))
    me = agent.casefold()
    chosen = None
    for names, group_rules in groups:  # 自分の名前に当たるグループ（最も長い一致）を優先、無ければ *
        best = max((len(n) for n in names if n != "*" and n in me), default=-1)
        if best >= 0 and (chosen is None or best > chosen[0]):
            chosen = (best, group_rules)
    if chosen is None:
        chosen = next(((0, r) for n, r in groups if "*" in n), None)
    if chosen is None:
        return True
    verdict, length = True, -1
    for is_allow, pattern in chosen[1]:
        if not pattern:
            continue
        if _pattern(pattern).match(path) and (len(pattern) > length or (len(pattern) == length and is_allow)):
            verdict, length = is_allow, len(pattern)
    return verdict


async def check(url: str, *, policy: egress.Policy | None = None, timeout: float = 10) -> tuple[bool, str]:
    """(取得してよいか, 理由)。理由は拒否のときだけ。"""
    parts = urlsplit(url)
    origin = f"{parts.scheme}://{parts.netloc}"
    now = time.monotonic()
    hit = _cache.get(origin)
    if hit is None or now - hit[0] > TTL_S:
        body, status = None, 0
        try:
            async with egress.guarded_client(timeout=timeout, policy=policy or egress.DEFAULT_POLICY,
                                             headers={"User-Agent": AGENT}) as client:
                url_robots = f"{origin}/robots.txt"
                for _ in range(3):  # robots.txt のリダイレクトは 3 段まで（各段は検査される）
                    response = await client.get(url_robots)
                    if response.status_code in (301, 302, 303, 307, 308) and response.headers.get("location"):
                        from urllib.parse import urljoin

                        url_robots = urljoin(url_robots, response.headers["location"])
                        continue
                    status = response.status_code
                    body = response.text[:500_000] if status < 400 else ""
                    break
        except Exception:  # noqa: BLE001 - 取得できなければ確かめられない
            body = None
        if body is not None and status >= 500:
            body = None
        hit = (now, body, status)
        _cache[origin] = hit
    _, body, status = hit
    if body is None:
        return False, "robots.txt を確かめられませんでした（確かめられないページはブラウザで開きません）"
    path = (parts.path or "/") + (f"?{parts.query}" if parts.query else "")
    if allowed(body, path):
        return True, ""
    return False, f"robots.txt が {parts.path or '/'} の取得を禁じています"
