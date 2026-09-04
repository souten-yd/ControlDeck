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
    # OpenAI互換の思考強度（low/medium/high/xhigh）。モデル個別設定から解決する。
    reasoning_effort: str | None = None
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
            # レベル指定/有効。llama.cpp等のjinjaテンプレートへ思考有効を明示する
            payload["chat_template_kwargs"] = {"enable_thinking": True}
        # 思考強度を解釈できるendpoint向け。llama.cppはinstanceの--reasoning-budgetが正なので、
        # ここは外部OpenAI互換endpointへの伝達手段として使う。
        if request.reasoning_effort and not request.disable_thinking:
            payload["reasoning_effort"] = request.reasoning_effort
        if request.response_format is not None:
            payload["response_format"] = self._response_format(request.response_format)
        return payload

    def _capacity_retries(self) -> int:
        """KV枯渇での再試行回数。共有KVを持つ管理下runtimeだけが上書きする。"""
        return 0

    async def _wait_for_capacity(self, request: RuntimeChatRequest) -> None:
        return None

    def _is_capacity_error(self, response: httpx.Response) -> bool:
        """KVプール枯渇による拒否か。

        llama.cpp は共有KVが尽きると 500 "Context size has been exceeded." を返す。
        単一リクエストがCTXを超える場合は 400 で別メッセージ（そちらは再試行しても無駄）。
        """
        if response.status_code != 500:
            return False
        message = ""
        try:
            message = str(response.json().get("error", {}).get("message") or "")
        except (ValueError, AttributeError, TypeError):
            message = ""
        if not message:
            # 本文がJSONでない/形が違う場合も取りこぼさない
            message = response.text or ""
        return "context size has been exceeded" in message.lower()

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
            for _ in range(self._capacity_retries()):
                if not self._is_capacity_error(response):
                    break
                # 共有KVが尽きて弾かれた。空くのを待って投げ直す。
                await self._wait_for_capacity(request)
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
    # KVプールの空き待ちの上限。超えたら待たずに投げる（従来どおりの挙動へ戻る）
    _CAPACITY_TIMEOUT_SECONDS = 120
    # KV枯渇で弾かれたときの再試行回数。空き容量は正確には予測できないため、
    # 予測を厳しくするのではなく、弾かれたら待って投げ直す方針で吸収する。
    _CAPACITY_RETRIES = 3

    async def _prepare(self, request: RuntimeChatRequest) -> None:
        await super()._prepare(request)
        # Ollamaの暗黙ロードと同等に、停止中のinstanceは生成前に自動起動して
        # /health 200（モデル読み込み完了）まで待つ。
        from app.models_mgmt import llama

        ok = await llama.ensure_ready_by_base_url(
            normalize_openai_base(request.base_url), timeout_seconds=self._READY_TIMEOUT_SECONDS,
        )
        if not ok:
            raise RuntimeProviderError("llama.cppの自動起動またはモデル読み込みに失敗しました")
        # KVプールの空きを待ってから投げる。
        # 共有KV(kv_unified)では総量が尽きると llama.cpp は待たずに 500 を返し、
        # しかも実行中の他リクエストごと巻き込んで失敗させる。先に空くのを待つ。
        from urllib.parse import urlsplit

        await self._wait_for_capacity(request)

    def _capacity_retries(self) -> int:
        return self._CAPACITY_RETRIES

    async def _wait_for_capacity(self, request: RuntimeChatRequest) -> None:
        """KVプールに空きが出るまで待つ。

        共有KV(kv_unified)では総量が尽きると llama.cpp は待たずに 500 を返し、
        実行中の他リクエストごと巻き込んで失敗させる。投げる前に待つ。
        空き容量は正確には予測できない（出力分の予約・プロンプトキャッシュ・断片化が
        効くため）ので、ここでは過度に厳しくせず、弾かれたら再試行する側で吸収する。
        """
        from urllib.parse import urlsplit

        from app.models_mgmt import llama

        port = urlsplit(request.base_url).port
        if not port:
            return
        # 必要量: プロンプト概算（4文字≒1token）+ 出力上限（予約される前提で見る）
        prompt_chars = sum(len(str(m.get("content") or "")) for m in request.messages)
        needed = prompt_chars // 4 + max(0, int(request.max_tokens or 0))
        await llama.await_capacity(port, needed, timeout_seconds=self._CAPACITY_TIMEOUT_SECONDS)


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

    @staticmethod
    def _native_messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """OpenAI互換のcontent配列（text+image_url data URL）をnative形式へ変換する。

        Ollama native /api/chat は content文字列 + images(base64配列) を取る。
        """
        converted: list[dict[str, Any]] = []
        for message in messages:
            content = message.get("content")
            if not isinstance(content, list):
                converted.append(message)
                continue
            text = "".join(str(part.get("text") or "") for part in content
                           if isinstance(part, dict) and part.get("type") == "text")
            images: list[str] = []
            for part in content:
                if not isinstance(part, dict) or part.get("type") != "image_url":
                    continue
                url = str((part.get("image_url") or {}).get("url") or "")
                if "base64," in url:
                    images.append(url.split("base64,", 1)[1])
            entry = {**message, "content": text}
            if images:
                entry["images"] = images
            converted.append(entry)
        return converted

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
            "model": request.model, "messages": self._native_messages(request.messages),
            "stream": stream, "think": request.thinking, "options": options,
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


class LuceboxRuntimeProvider(OpenAICompatibleRuntimeProvider):
    """Lucebox（dflash_server）。OpenAI Chat Completions 互換。

    llama.cpp と違い共有KVプールを持たず、max-ctx 固定の単一セッション構成なので、
    KV 空き待ちは行わない。停止中のオンデマンド起動だけ llama.cpp と揃える。
    """

    kind = "lucebox"

    # DFlash はターゲット+ドラフトの2本を読むため、初回ロードは llama.cpp より長い。
    _READY_TIMEOUT_SECONDS = 420

    def _payload(self, request: RuntimeChatRequest, *, stream: bool) -> dict[str, Any]:
        """投機デコードを優先する設定なら temperature を 0 へ固定する。

        Lucebox の DFlash2 検証は厳密グリーディのみで、temperature>0 だと投機経路を
        使わず自己回帰へ落ちる（同一プロンプトの実測で 142 tok/s → 29 tok/s）。
        呼び出し側は既定 0.4 を送るため、ここで潰さないと Lucebox を選んでも
        速度が出ない。切りたい利用者はモデル個別設定の prefer_speculative を外す。
        """
        payload = super()._payload(request, stream=stream)
        if self._prefers_speculative(request.base_url):
            payload["temperature"] = 0.0
        return payload

    @staticmethod
    def _prefers_speculative(base_url: str) -> bool:
        from app.models_mgmt import lucebox

        parsed = urlsplit(normalize_openai_base(base_url))
        if parsed.hostname not in ("127.0.0.1", "localhost", "::1") or not parsed.port:
            return False
        return lucebox.pins_greedy_sampling(port=parsed.port)

    async def _prepare(self, request: RuntimeChatRequest) -> None:
        await super()._prepare(request)
        from app.models_mgmt import lucebox

        ok = await lucebox.ensure_ready_by_base_url(
            normalize_openai_base(request.base_url), timeout_seconds=self._READY_TIMEOUT_SECONDS,
        )
        if not ok:
            raise RuntimeProviderError("Luceboxの自動起動またはモデル読み込みに失敗しました")


_OLLAMA = OllamaRuntimeProvider()
_LLAMA = LlamaCppRuntimeProvider()
_LUCEBOX = LuceboxRuntimeProvider()
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
        from app.models_mgmt import llama, lucebox

        parsed = urlsplit(normalized)
        if parsed.hostname in ("127.0.0.1", "localhost", "::1"):
            if parsed.port in llama.endpoint_ports():
                return _LLAMA
            if parsed.port in lucebox.endpoint_ports():
                return _LUCEBOX
    except Exception:
        pass
    return _OPENAI


def resolve_target(base_url: str, model: str) -> tuple[str, str]:
    """ゲートウェイ宛の接続先を実エンドポイントへ解決する。

    UI・OpenCode・ワークフローはゲートウェイの1アドレスを指すが、ControlDeck内部の
    生成は自分のHTTPへ戻らず実インスタンスを直接叩く。ホップを増やさずに済み、
    thinking解決・キャンセル・KV受け入れ制御など既存のprovider処理もそのまま効く。
    """
    from app.models_mgmt import gateway

    return gateway.resolve_internal_target(base_url, model)


def provider_for_request(request: RuntimeChatRequest) -> LlmRuntimeProvider:
    """requestの接続先を解決したうえでproviderを選ぶ。requestも解決後の値へ揃える。"""
    request.base_url, request.model = resolve_target(request.base_url, request.model)
    return provider_for_base_url(request.base_url)


_ALL_PROVIDERS = (_OLLAMA, _LLAMA, _LUCEBOX, _OPENAI)


def active_request_count() -> int:
    return sum(provider.active_request_count for provider in _ALL_PROVIDERS)


async def cancel_request(request_id: str) -> bool:
    """provider種別を知らない上位job/APIから生成を明示取消する。"""
    results = await asyncio.gather(*(provider.cancel(request_id) for provider in _ALL_PROVIDERS))
    return any(results)
