"""Managed LLM runtimeのオンデマンド起動・モデルロード。

Workflow等の利用側はHTTP接続失敗を待ってから推測で再試行せず、生成前にこの境界を
呼ぶ。ControlDeckが管理するローカルendpointだけを操作し、外部endpointは素通しする。
"""
from __future__ import annotations

import asyncio
from urllib.parse import urlsplit

from app.models_mgmt import llama, ollama, provider_adapters


class RuntimeStartupError(RuntimeError):
    """管理runtimeの起動またはモデルロードに失敗した。"""


_locks: dict[str, asyncio.Lock] = {}


def _endpoint_key(base_url: str) -> tuple[str, int]:
    parsed = urlsplit(base_url.rstrip("/"))
    host = (parsed.hostname or "").lower()
    if host in {"localhost", "::1"}:
        host = "127.0.0.1"
    return host, parsed.port or (443 if parsed.scheme == "https" else 80)


async def ensure_chat_model_ready(
    base_url: str,
    model: str,
    *,
    keep_alive: str | int | None = None,
    timeout_seconds: float = 240,
) -> dict[str, str | bool]:
    """管理対象なら起動・ロード完了まで待ち、外部endpointなら変更しない。"""
    timeout = max(10.0, min(float(timeout_seconds), 600.0))
    key = f"{base_url.rstrip('/')}::{model}"
    lock = _locks.setdefault(key, asyncio.Lock())
    async with lock:
        try:
            ready = await llama.ensure_ready_by_base_url(base_url, timeout_seconds=int(timeout))
        except Exception as exc:
            raise RuntimeStartupError(f"llama.cppの自動起動に失敗しました: {exc}") from exc
        if not ready:
            raise RuntimeStartupError("llama.cppの自動起動またはモデル読み込みが時間内に完了しませんでした")

        try:
            is_ollama = _endpoint_key(base_url) == _endpoint_key(ollama.base_url())
        except ValueError:
            is_ollama = False
        if not is_ollama:
            return {"managed": False, "runtime": "external", "ready": True}

        try:
            # REST APIと同じadapterを通し、GPU policyと同時ロード上限を共通適用する。
            await asyncio.wait_for(provider_adapters.load_model("ollama", model, keep_alive), timeout=timeout)
        except TimeoutError as exc:
            raise RuntimeStartupError(f"Ollamaモデル「{model}」のロードが{int(timeout)}秒以内に完了しませんでした") from exc
        except provider_adapters.ProviderError as exc:
            raise RuntimeStartupError(f"Ollamaモデル「{model}」を自動ロードできませんでした: {exc}") from exc
        return {"managed": True, "runtime": "ollama", "ready": True}
