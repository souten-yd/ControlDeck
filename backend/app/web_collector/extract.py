"""本文抽出の窓口。機能「Web 本文抽出（Trafilatura）」が導入されていれば専用 venv の worker で、無ければ BeautifulSoup で。

worker は 1 つを使い回し（Trafilatura と lxml の読み込みは 1 回だけ）、1 件ごとに時間の上限を付ける。
上限を超えたり壊れたりしたら worker を殺して次の要求で起こし直す。メモリは RLIMIT_AS で上限を付ける。
"""
from __future__ import annotations

import asyncio
import itertools
import json
import logging
import os
import resource
from pathlib import Path

logger = logging.getLogger("control_deck.web_collector.extract")

FEATURE_ID = "web-extract"
MODES = ("article", "documentation", "full_text")
TIMEOUT_S = 30
MEMORY_BYTES = 1536 * 1024 * 1024
MAX_HTML_CHARS = 8_000_000
WORKER = Path(__file__).with_name("worker.py")

_BLOCK_TAGS = ("p", "div", "section", "article", "li", "tr", "br", "h1", "h2", "h3", "h4", "h5", "h6", "pre",
               "blockquote", "table", "ul", "ol", "dd", "dt")


def worker_python() -> Path | None:
    """導入済みなら専用 venv の Python。"""
    from app.features import registry

    python = registry._feature_root(FEATURE_ID) / "venv" / "bin" / "python"  # noqa: SLF001
    return python if python.is_file() else None


def _limits() -> None:  # 子プロセスの中で動く
    resource.setrlimit(resource.RLIMIT_AS, (MEMORY_BYTES, MEMORY_BYTES))
    os.setsid()


class _Worker:
    def __init__(self) -> None:
        self.proc: asyncio.subprocess.Process | None = None
        self.python: Path | None = None
        self.lock = asyncio.Lock()
        self.ids = itertools.count(1)

    async def _start(self, python: Path) -> None:
        self.proc = await asyncio.create_subprocess_exec(
            str(python), "-I", str(WORKER), stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL, preexec_fn=_limits, limit=64 * 1024 * 1024,
            env={"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8", "PYTHONIOENCODING": "utf-8"})
        self.python = python

    async def stop(self) -> None:
        proc, self.proc = self.proc, None
        if proc is not None and proc.returncode is None:
            proc.kill()
            await proc.wait()

    async def run(self, python: Path, payload: dict) -> dict:
        async with self.lock:
            if self.proc is None or self.proc.returncode is not None or self.python != python:
                await self.stop()
                await self._start(python)
            assert self.proc is not None and self.proc.stdin is not None and self.proc.stdout is not None
            payload = {**payload, "id": next(self.ids)}
            try:
                self.proc.stdin.write((json.dumps(payload, ensure_ascii=False) + "\n").encode())
                await self.proc.stdin.drain()
                line = await asyncio.wait_for(self.proc.stdout.readline(), TIMEOUT_S)
            except (asyncio.TimeoutError, BrokenPipeError, ConnectionResetError):
                await self.stop()
                raise
            if not line:
                await self.stop()
                raise RuntimeError("抽出の worker が終了しました")
            out = json.loads(line)
            if out.get("id") != payload["id"]:
                await self.stop()
                raise RuntimeError("抽出の worker の応答がずれました")
            return out


_worker = _Worker()


def fallback(html: str, url: str = "") -> dict:
    """Trafilatura が無いとき・失敗したときの抽出。見出し・段落・表の行を改行で残す（以前は 1 行に連結していた）。"""
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, "html.parser")
    title = (soup.title.get_text(" ", strip=True) if soup.title else "")[:500]
    for tag in soup(["script", "style", "nav", "footer", "header", "aside", "noscript", "template", "form"]):
        tag.decompose()
    for tag in soup.find_all(_BLOCK_TAGS):
        tag.insert_before("\n")
    for level in range(1, 7):
        for h in soup.find_all(f"h{level}"):
            h.insert_before("#" * level + " ")
    lines = [" ".join(line.split()) for line in soup.get_text().splitlines()]
    text = "\n".join(line for line in lines if line)
    links = []
    for a in soup.find_all("a", href=True)[:500]:
        href = str(a["href"])
        if href.startswith(("http://", "https://")):
            links.append({"url": href.split("#", 1)[0], "text": a.get_text(" ", strip=True)[:200]})
    return {"markdown": text, "title": title, "metadata": {"title": title} if title else {}, "links": links,
            "visible_chars": len(text), "warnings": ["trafilatura_not_installed"], "extractor": "beautifulsoup"}


async def extract(html: str, url: str = "", mode: str = "article") -> dict:
    """HTML から本文（Markdown）・題名・メタデータ・リンクを取り出す。"""
    if mode not in MODES:
        mode = "article"
    html = html[:MAX_HTML_CHARS]
    python = worker_python()
    if python is not None:
        try:
            out = await _worker.run(python, {"html": html, "url": url, "mode": mode})
            if out.get("ok"):
                out.pop("ok", None)
                out.pop("id", None)
                return out
            logger.warning("Trafilatura の抽出に失敗しました（従来の抽出に戻します）: %s", out.get("error"))
        except (asyncio.TimeoutError, RuntimeError, OSError, json.JSONDecodeError) as exc:
            logger.warning("抽出の worker が応答しません（従来の抽出に戻します）: %s", exc)
        result = await asyncio.to_thread(fallback, html, url)
        result["warnings"] = ["trafilatura_failed_fallback"]
        return result
    return await asyncio.to_thread(fallback, html, url)


async def worker_version(python: Path | None = None) -> str:
    python = python or worker_python()
    if python is None:
        return ""
    proc = await asyncio.create_subprocess_exec(str(python), "-I", str(WORKER), "--version",
                                                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
    out, _ = await asyncio.wait_for(proc.communicate(), 30)
    return out.decode().strip() if proc.returncode == 0 else ""


async def shutdown() -> None:
    await _worker.stop()


__all__ = ["extract", "fallback", "worker_python", "worker_version", "shutdown", "MODES"]
