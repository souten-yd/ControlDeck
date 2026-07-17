"""LLM runtimeの生成処理をprovider差分から分離する共通契約。"""
from __future__ import annotations

import asyncio
import json
import uuid
from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any, Literal
from urllib.parse import urlsplit

import httpx


class RuntimeProviderError(RuntimeError):
    """provider固有情報や秘密値を含めない生成エラー。"""


class GenerationCancelled(RuntimeProviderError):
    pass


@dataclass(slots=True)
class RuntimeChatRequest:
    base_url: str
    model: str
    messages: list[dict[str, Any]]
    api_key: str = ""
    temperature: float = 0.4
    max_tokens: int = 2048
    thinking: bool | str | None = None
    disable_thinking: bool = False
    response_format: dict[str, Any] | None = None
    keep_alive: str | int | None = None
    # Deep Research等の大規模入力で要求するcontext。providerがrequest単位で対応する場合だけ使う。
    context_window: int | None = None
    timeout_seconds: int = 300


@dataclass(slots=True)
class RuntimeChunk:
    type: Literal["content", "thinking", "usage"]
    content: str = ""
    usage: dict[str, Any] = field(default_factory=dict)


def normalize_openai_base(base_url: str) -> str:
    base = base_url.strip().rstrip("/")
    return base if base.endswith("/v1") else base + "/v1"


def normalize_response_format(value: dict[str, Any]) -> dict[str, Any]:
    """内部の簡略schema表現をOpenAI互換の標準payloadへ正規化する。"""
    if value.get("type") == "json_schema" and "schema" in value:
        return {
            "type": "json_schema",
            "json_schema": {
                "name": str(value.get("name") or "structured_output"),
                "schema": value["schema"],
                "strict": bool(value.get("strict", True)),
            },
        }
    return value


def response_format_candidates(value: dict[str, Any] | None) -> list[dict[str, Any] | None]:
    """provider差を吸収する構造化出力dialectの優先順。

    OpenAI標準JSON Schemaを第一候補にし、未対応runtimeではJSON Object、最後に
    prompt制約のみへ段階的に退避する。Ollama/llama.cpp/vLLM/外部OpenAI互換で共有する。
    """
    if value is None:
        return [None]
    normalized = normalize_response_format(value)
    if normalized.get("type") == "json_schema":
        return [normalized, {"type": "json_object"}, None]
    if normalized.get("type") == "json_object":
        return [normalized, None]
    return [normalized, None]


class LlmRuntimeProvider(ABC):
    kind = "unknown"

    def __init__(self) -> None:
        self._active: dict[str, asyncio.Event] = {}

    def get_capabilities(self) -> set[str]:
        return {"chat", "stream", "cancel"}

    @property
    def active_request_count(self) -> int:
        return len(self._active)

    async def complete(self, request: RuntimeChatRequest) -> str:
        await self._prepare(request)
        return await self._complete_impl(request)

    async def stream_chat(
        self, request: RuntimeChatRequest, *, request_id: str | None = None,
    ) -> AsyncIterator[RuntimeChunk]:
        identifier = request_id or uuid.uuid4().hex
        if identifier in self._active:
            raise RuntimeProviderError("同じrequest IDの生成が既に実行中です")
        cancel_event = asyncio.Event()
        self._active[identifier] = cancel_event
        try:
            await self._prepare(request)
            async for chunk in self._stream_impl(request, cancel_event):
                if cancel_event.is_set():
                    raise GenerationCancelled("生成をキャンセルしました")
                yield chunk
            if cancel_event.is_set():
                raise GenerationCancelled("生成をキャンセルしました")
        finally:
            self._active.pop(identifier, None)

    async def cancel(self, request_id: str) -> bool:
        event = self._active.get(request_id)
        if event is None:
            return False
        event.set()
        return True

    async def _prepare(self, request: RuntimeChatRequest) -> None:
        from app.models_mgmt.runtime_policy import ensure_gpu_profile

        try:
            await asyncio.to_thread(ensure_gpu_profile, base_url=request.base_url)
        except RuntimeError as exc:
            raise RuntimeProviderError(str(exc)) from exc

    @abstractmethod
    async def _complete_impl(self, request: RuntimeChatRequest) -> str: ...

    @abstractmethod
    async def _stream_impl(
        self, request: RuntimeChatRequest, cancel_event: asyncio.Event,
    ) -> AsyncIterator[RuntimeChunk]: ...


class OpenAICompatibleRuntimeProvider(LlmRuntimeProvider):
    kind = "openai-compatible"

    @staticmethod
    def _response_format(value: dict[str, Any]) -> dict[str, Any]:
        return normalize_response_format(value)

    def _payload(self, request: RuntimeChatRequest, *, stream: bool) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": request.model,
            "messages": request.messages,
            "temperature": request.temperature,
            "stream": stream,
            "max_tokens": request.max_tokens,
        }
        if request.keep_alive is not None:
            payload["keep_alive"] = request.keep_alive
        if request.disable_thinking or request.thinking is False:
            payload["chat_template_kwargs"] = {"enable_thinking": False}
        elif request.thinking is True or isinstance(request.thinking, str):
            # 共通設定「オン」/レベル指定。llama.cpp等のjinjaテンプレートへ思考有効を明示する
            payload["chat_template_kwargs"] = {"enable_thinking": True}
        if request.response_format is not None:
            payload["response_format"] = self._response_format(request.response_format)
        return payload

    async def _post(self, request: RuntimeChatRequest, payload: dict[str, Any]) -> httpx.Response:
        async with httpx.AsyncClient(timeout=request.timeout_seconds) as client:
            return await client.post(
                normalize_openai_base(request.base_url) + "/chat/completions",
                json=payload,
                headers={"Authorization": f"Bearer {request.api_key or 'sk-no-key'}"},
            )

    async def _complete_impl(self, request: RuntimeChatRequest) -> str:
        payload = self._payload(request, stream=False)
        response: httpx.Response | None = None
        for candidate in response_format_candidates(request.response_format):
            attempt = dict(payload)
            if candidate is None:
                attempt.pop("response_format", None)
            else:
                attempt["response_format"] = candidate
            response = await self._post(request, attempt)
            if response.status_code < 400:
                break
            # 認証失敗、rate limit、provider内部障害はdialect差ではないので再送しない。
            if response.status_code not in {400, 404, 415, 422, 501}:
                break
        if response is None:
            raise RuntimeProviderError("LLM応答がありません")
        if response.status_code >= 400:
            raise RuntimeProviderError(f"LLM HTTPエラー {response.status_code}")
        try:
            message = response.json()["choices"][0]["message"]
            return str(message.get("content") or "")
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            raise RuntimeProviderError("LLM応答の形式が不正です") from exc

    async def _stream_impl(
        self, request: RuntimeChatRequest, cancel_event: asyncio.Event,
    ) -> AsyncIterator[RuntimeChunk]:
        payload = self._payload(request, stream=True)
        # 最終chunkで正確なprompt/completionトークン数を得る（OpenAI標準。未対応serverは無視する）
        payload["stream_options"] = {"include_usage": True}
        async with httpx.AsyncClient(timeout=None) as client:
            async with client.stream(
                "POST", normalize_openai_base(request.base_url) + "/chat/completions",
                json=payload,
                headers={"Authorization": f"Bearer {request.api_key or 'sk-no-key'}"},
            ) as response:
                if response.status_code >= 400:
                    raise RuntimeProviderError(f"LLM HTTPエラー {response.status_code}")
                async for line in response.aiter_lines():
                    if cancel_event.is_set():
                        raise GenerationCancelled("生成をキャンセルしました")
                    if not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    if data == "[DONE]":
                        break
                    try:
                        item = json.loads(data)
                    except (json.JSONDecodeError, TypeError):
                        continue
                    usage = item.get("usage")
                    try:
                        delta = item["choices"][0]["delta"]
                    except (KeyError, IndexError, TypeError):
                        delta = {}
                    if isinstance(delta, dict):
                        reasoning = str(delta.get("reasoning_content") or "")
                        content = str(delta.get("content") or "")
                        if reasoning:
                            yield RuntimeChunk("thinking", content=reasoning)
                        if content:
                            yield RuntimeChunk("content", content=content)
                    if isinstance(usage, dict):
                        yield RuntimeChunk("usage", usage=usage)


class LlamaCppRuntimeProvider(OpenAICompatibleRuntimeProvider):
    kind = "llama.cpp"

    # モデル読み込み（大型GGUFで数十秒〜数分）を待つ上限
    _READY_TIMEOUT_SECONDS = 240

    async def _prepare(self, request: RuntimeChatRequest) -> None:
        await super()._prepare(request)
        # Ollamaの暗黙ロードと同等に、停止中のinstanceは生成前に自動起動して
        # /health 200（モデル読み込み完了）まで待つ。
        from app.models_mgmt import llama

        parsed = urlsplit(normalize_openai_base(request.base_url))
        instances = llama.get_config()["instances"]
        alias = next(
            (name for name, item in instances.items() if int(item.get("port", 0)) == parsed.port),
            None,
        )
        if alias is None:
            return
        if (await llama.health(alias)).get("ok"):
            return
        ok, error = await asyncio.to_thread(llama.start_instance, alias)
        if not ok:
            raise RuntimeProviderError(f"llama.cppの自動起動に失敗しました: {error}")
        deadline = asyncio.get_event_loop().time() + self._READY_TIMEOUT_SECONDS
        while asyncio.get_event_loop().time() < deadline:
            if (await llama.health(alias)).get("ok"):
                return
            await asyncio.sleep(2)
        raise RuntimeProviderError("llama.cppのモデル読み込みが時間内に完了しませんでした")


class OllamaRuntimeProvider(OpenAICompatibleRuntimeProvider):
    kind = "ollama"

    @staticmethod
    def _native_base(base_url: str) -> str:
        base = normalize_openai_base(base_url)
        return base[:-3].rstrip("/")

    def _use_native(self, request: RuntimeChatRequest) -> bool:
        # Ollama native APIはthink無効化とJSON Schema(format)を同時に扱える。
        # OpenAI互換APIでは一部thinking modelがchat_template_kwargsを無視し、推論だけで
        # max_tokensを使い切ってJSONが途中切れになるため、どちらか必要ならnativeを使う。
        return request.thinking is not None or request.response_format is not None or request.context_window is not None

    def _native_payload(
        self, request: RuntimeChatRequest, *, stream: bool,
        response_format: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        options: dict[str, Any] = {
            "temperature": request.temperature,
            "num_predict": request.max_tokens,
        }
        if request.context_window is not None:
            options["num_ctx"] = request.context_window
        payload: dict[str, Any] = {
            "model": request.model, "messages": request.messages, "stream": stream,
            "think": request.thinking, "options": options,
        }
        if request.keep_alive is not None:
            payload["keep_alive"] = request.keep_alive
        if response_format is not None:
            if response_format.get("type") == "json_schema":
                schema = response_format.get("schema")
                if schema is None and isinstance(response_format.get("json_schema"), dict):
                    schema = response_format["json_schema"].get("schema")
                if isinstance(schema, dict):
                    payload["format"] = schema
            elif response_format.get("type") == "json_object":
                payload["format"] = "json"
        return payload

    async def _complete_impl(self, request: RuntimeChatRequest) -> str:
        if not self._use_native(request):
            return await super()._complete_impl(request)
        response: httpx.Response | None = None
        for candidate in response_format_candidates(request.response_format):
            async with httpx.AsyncClient(timeout=request.timeout_seconds) as client:
                response = await client.post(
                    self._native_base(request.base_url) + "/api/chat",
                    json=self._native_payload(request, stream=False, response_format=candidate),
                )
            if response.status_code < 400 or response.status_code not in {400, 404, 415, 422, 501}:
                break
        if response is None:
            raise RuntimeProviderError("LLM応答がありません")
        if response.status_code >= 400:
            raise RuntimeProviderError(f"LLM HTTPエラー {response.status_code}")
        try:
            return str(response.json()["message"].get("content") or "")
        except (ValueError, KeyError, TypeError) as exc:
            raise RuntimeProviderError("LLM応答の形式が不正です") from exc

    async def _stream_impl(
        self, request: RuntimeChatRequest, cancel_event: asyncio.Event,
    ) -> AsyncIterator[RuntimeChunk]:
        if not self._use_native(request):
            async for chunk in super()._stream_impl(request, cancel_event):
                yield chunk
            return
        async with httpx.AsyncClient(timeout=None) as client:
            async with client.stream(
                "POST", self._native_base(request.base_url) + "/api/chat",
                json=self._native_payload(request, stream=True, response_format=request.response_format),
            ) as response:
                if response.status_code >= 400:
                    raise RuntimeProviderError(f"LLM HTTPエラー {response.status_code}")
                async for line in response.aiter_lines():
                    if cancel_event.is_set():
                        raise GenerationCancelled("生成をキャンセルしました")
                    if not line.strip():
                        continue
                    try:
                        item = json.loads(line)
                        message = item.get("message", {})
                    except (json.JSONDecodeError, TypeError):
                        continue
                    thinking = str(message.get("thinking") or "")
                    content = str(message.get("content") or "")
                    if thinking:
                        yield RuntimeChunk("thinking", content=thinking)
                    if content:
                        yield RuntimeChunk("content", content=content)
                    if item.get("done"):
                        # native APIの最終chunkにある実測トークン数をOpenAI形式へ揃えて流す
                        yield RuntimeChunk("usage", usage={
                            "prompt_tokens": item.get("prompt_eval_count"),
                            "completion_tokens": item.get("eval_count"),
                        })


_OLLAMA = OllamaRuntimeProvider()
_LLAMA = LlamaCppRuntimeProvider()
_OPENAI = OpenAICompatibleRuntimeProvider()


def provider_for_base_url(base_url: str) -> LlmRuntimeProvider:
    """管理中endpointを識別し、外部互換endpointは汎用providerへfallbackする。"""
    normalized = normalize_openai_base(base_url)
    try:
        from app.models_mgmt import ollama

        if normalized == normalize_openai_base(ollama.base_url()):
            return _OLLAMA
    except Exception:
        pass
    try:
        from app.models_mgmt import llama

        parsed = urlsplit(normalized)
        if parsed.hostname in ("127.0.0.1", "localhost", "::1"):
            ports = {int(item.get("port", 0)) for item in llama.list_instances()}
            if parsed.port in ports:
                return _LLAMA
    except Exception:
        pass
    return _OPENAI


def active_request_count() -> int:
    return sum(provider.active_request_count for provider in (_OLLAMA, _LLAMA, _OPENAI))


async def cancel_request(request_id: str) -> bool:
    """provider種別を知らない上位job/APIから生成を明示取消する。"""
    results = await asyncio.gather(*(provider.cancel(request_id) for provider in (_OLLAMA, _LLAMA, _OPENAI)))
    return any(results)
