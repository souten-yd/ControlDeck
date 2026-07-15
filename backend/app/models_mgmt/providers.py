"""LLMランタイムを共通形式で検出するproviderカタログ。"""
from __future__ import annotations

import asyncio
import logging
from urllib.parse import urlsplit

import httpx

logger = logging.getLogger(__name__)

_KNOWN_LOCAL = {
    11434: ("ollama", "Ollama"),
    8080: ("llama.cpp", "llama.cpp"),
    1234: ("lm-studio", "LM Studio"),
    8000: ("openai-compatible", "OpenAI互換"),
    5001: ("openai-compatible", "OpenAI互換"),
}


def _openai_base(url: str) -> str:
    base = url.rstrip("/")
    return base if base.endswith("/v1") else base + "/v1"


def _provider_id(kind: str, base_url: str, *, managed: bool) -> str:
    parsed = urlsplit(base_url)
    host = parsed.hostname or "unknown"
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    return kind if managed and kind in ("ollama", "llama.cpp") else f"{kind}-{host}-{port}"


async def _candidates() -> list[dict]:
    from app.models_mgmt import llama, ollama

    candidates: dict[str, dict] = {}

    def add(base_url: str, kind: str, name: str, *, managed: bool = False,
            installed: bool | None = None, experimental: bool = False) -> None:
        base = _openai_base(base_url)
        current = candidates.get(base, {})
        effective_managed = managed or current.get("managed", False)
        effective_kind = current.get("provider", kind) if current.get("managed") else kind
        candidates[base] = {
            "id": _provider_id(effective_kind, base, managed=effective_managed),
            "provider": effective_kind, "name": current.get("name", name) if current.get("managed") else name,
            "base_url": base, "managed": effective_managed,
            "installed": installed if installed is not None else current.get("installed"),
            "experimental": experimental or current.get("experimental", False),
        }

    add(ollama.base_url(), "ollama", "Ollama", managed=True)
    llama_status = llama.runtime_status()
    if llama_status.get("base_url"):
        add(str(llama_status["base_url"]), "llama.cpp", "llama.cpp", managed=True,
            installed=bool(llama_status.get("installed")), experimental=True)
    else:
        port = int(llama_status.get("port") or 8080)
        add(f"http://127.0.0.1:{port}", "llama.cpp", "llama.cpp", managed=True,
            installed=False, experimental=True)

    for port, (kind, name) in _KNOWN_LOCAL.items():
        add(f"http://127.0.0.1:{port}", kind, name)

    try:
        from app.applications import service as apps
        from app.database import SessionLocal
        from app.models import ManagedApplication

        def managed_ports() -> set[int]:
            db = SessionLocal()
            try:
                found: set[int] = set()
                for app in db.query(ManagedApplication).all():
                    found.update(apps.runtime_info(app).listening_ports or [])
                return found
            finally:
                db.close()

        for port in await asyncio.to_thread(managed_ports):
            add(f"http://127.0.0.1:{port}", "openai-compatible", "管理アプリ")
    except Exception:
        logger.exception("failed to collect managed application ports for LLM discovery")

    return list(candidates.values())


async def list_providers(*, include_unavailable: bool = True, exclude_port: int | None = None) -> list[dict]:
    """候補の `/v1/models` を並列確認し、共通provider形式で返す。"""
    candidates = await _candidates()

    async def probe(item: dict) -> dict | None:
        parsed = urlsplit(item["base_url"])
        if exclude_port and parsed.hostname in ("127.0.0.1", "localhost", "::1") and parsed.port == exclude_port:
            return None
        try:
            async with httpx.AsyncClient(timeout=1.5) as client:
                response = await client.get(item["base_url"] + "/models")
            if response.status_code != 200:
                raise httpx.HTTPStatusError("unexpected status", request=response.request, response=response)
            payload = response.json()
            models = [m.get("id", "") for m in payload.get("data", []) if isinstance(m, dict)]
            return {**item, "available": True, "models": [m for m in models if m][:50]}
        except (httpx.HTTPError, ValueError, TypeError):
            if include_unavailable and item.get("managed"):
                return {**item, "available": False, "models": []}
            return None

    results = await asyncio.gather(*(probe(item) for item in candidates))
    return sorted((item for item in results if item is not None), key=lambda x: (not x["managed"], x["name"], x["base_url"]))
