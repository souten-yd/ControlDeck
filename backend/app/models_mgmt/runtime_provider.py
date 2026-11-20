"""LLM runtimeの生成処理をprovider差分から分離する共通契約。"""
from __future__ import annotations

import asyncio
import json
import ipaddress
import uuid
from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any, Literal
from urllib.parse import urlsplit

import httpx



def _timing_target(base_url: str) -> tuple[int, bool]:
    """(ポート, ローカルのエンジンか)。外部モデルはゲートウェイ自身へ送られ（cloud:）、外の URL は
    この PC で動いていないので、LLM の電気代の「処理中」に数えない。"""
    from app.models_mgmt.gateway import is_gateway_url

    base = normalize_openai_base(base_url)
    parts = urlsplit(base)
    host = (parts.hostname or "").lower()
    local = host in ("127.0.0.1", "localhost", "::1") and not is_gateway_url(base)
    return parts.port or 0, local

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
    # None は「送らない」: 各モデルの設定（llama.cpp の個別設定、Strata / Maya-S のモデルカード
    # 推奨値、Ollama の Modelfile）に任せる。構造化生成などで決め打ちしたい呼び出しだけ数値を渡す。
    temperature: float | None = 0.4
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
    local_only: bool = False
    # ゲートウェイへ送るときに名乗る呼び出し元（外部モデルの許可はこの単位）。
    # 既定は許可の対象外（internal → ゲートウェイでは api 扱い）。AI アシスタントの経路だけが
    # 明示的に "assistant" を名乗る。Add-on などが黙ってアシスタントの許可を使わないため。
    client: str = "internal"
    # モデル自身が呼ぶ道具（OpenAI 互換の tools）。渡したときだけ送る。呼ばれたら "tool_call" の chunk で返す
    tools: list[dict[str, Any]] | None = None


@dataclass(slots=True)
class RuntimeChunk:
    type: Literal["content", "thinking", "usage", "tool_call"]
    content: str = ""
    usage: dict[str, Any] = field(default_factory=dict)
    # type == "tool_call": [{"id", "name", "arguments"（JSON 文字列）}]
    tool_calls: list[dict[str, Any]] = field(default_factory=list)


class ToolsUnsupported(RuntimeError):
    """endpoint が tools 付きの要求を受け付けなかった（道具を呼べないモデル・runtime）。呼び出し側は道具なしで続ける。"""


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
    # 会話で道具（tool calling）を渡してよいか。実機で「呼ぶ・結果を受けて続ける」を確かめた runtime だけ True にする。
    # 道具を黙って無視する runtime に渡すと、資料を一度も探さなくなるため（2026-11-20）
    tools_verified = False

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
        if request.local_only:
            # ゲートウェイは loopback だが、cloud: はそこから外部へ出る。ローカル専用の要求では通さない。
            if str(request.model or "").startswith("cloud:"):
                raise RuntimeProviderError("local-only transport cannot use an external model")
            try:
                url = urlsplit(request.base_url)
                if (url.scheme != "http" or not ipaddress.ip_address(url.hostname or "").is_loopback
                        or not url.port or url.username or url.password or url.query or url.fragment):
                    raise ValueError()
            except ValueError as exc:
                raise RuntimeProviderError("local-only transport requires a loopback endpoint") from exc
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


def _request_headers(request: RuntimeChatRequest) -> dict[str, str]:
    """生成要求のヘッダ。ゲートウェイ宛にだけ呼び出し元を名乗る（1 か所で付ける）。"""
    from app.models_mgmt import gateway

    return {"Authorization": f"Bearer {gateway.internal_api_key(request.base_url, request.api_key)}",
            **gateway.client_headers(request.base_url, request.client)}


def _http_error(request: RuntimeChatRequest, status_code: int, body: bytes | str = b"") -> RuntimeProviderError:
    """ゲートウェイが返した理由（外部送信が未許可・上限額など）は利用者へそのまま伝える。"""
    from app.models_mgmt import gateway

    detail = ""
    if gateway.is_gateway_url(request.base_url):
        try:
            detail = str(json.loads(body).get("detail") or "")[:300]
        except (ValueError, AttributeError, TypeError):
            detail = ""
    return RuntimeProviderError(detail or f"LLM HTTPエラー {status_code}")


class OpenAICompatibleRuntimeProvider(LlmRuntimeProvider):
    kind = "openai-compatible"

    @staticmethod
    def _response_format(value: dict[str, Any]) -> dict[str, Any]:
        return normalize_response_format(value)

    def _payload(self, request: RuntimeChatRequest, *, stream: bool) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": request.model,
            "messages": request.messages,
            "stream": stream,
            "max_tokens": request.max_tokens,
        }
        if request.temperature is not None:
            payload["temperature"] = request.temperature
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
        if request.tools:
            payload["tools"] = request.tools
            payload["tool_choice"] = "auto"
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
        async with httpx.AsyncClient(timeout=request.timeout_seconds, trust_env=not request.local_only, follow_redirects=False) as client:
            return await client.post(
                normalize_openai_base(request.base_url) + "/chat/completions",
                json=payload,
                headers=_request_headers(request),
            )

    async def _complete_impl(self, request: RuntimeChatRequest) -> str:
        from app.models_mgmt import live_stats
        port, local = _timing_target(request.base_url)
        timing = live_stats.begin(port, local=local)
        body: dict = {}
        try:
            text, body = await self._complete_body(request)
            return text
        finally:
            live_stats.end(timing, body.get("usage"), body.get("timings"))

    async def _complete_body(self, request: RuntimeChatRequest) -> tuple[str, dict]:
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
        if response.status_code >= 300:
            raise _http_error(request, response.status_code, response.content)
        try:
            body = response.json()
            message = body["choices"][0]["message"]
            return str(message.get("content") or ""), body
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            raise RuntimeProviderError("LLM応答の形式が不正です") from exc

    async def _stream_impl(
        self, request: RuntimeChatRequest, cancel_event: asyncio.Event,
    ) -> AsyncIterator[RuntimeChunk]:
        payload = self._payload(request, stream=True)
        # 最終chunkで正確なprompt/completionトークン数を得る（OpenAI標準。未対応serverは無視する）
        payload["stream_options"] = {"include_usage": True}
        from app.models_mgmt import live_stats
        port, local = _timing_target(request.base_url)
        timing = live_stats.begin(port, local=local)
        final_usage = None
        try:
            async for chunk in self._stream_lines(request, payload, cancel_event):
                if chunk.type in ("thinking", "content"):
                    live_stats.delta(timing, thinking=chunk.type == "thinking")
                elif chunk.type == "usage":
                    final_usage = chunk.usage
                yield chunk
        finally:
            live_stats.end(timing, final_usage)  # streamed: ControlDeck's own timing

    async def _stream_lines(
        self, request: RuntimeChatRequest, payload: dict[str, Any], cancel_event: asyncio.Event,
    ) -> AsyncIterator[RuntimeChunk]:
        async with httpx.AsyncClient(timeout=None, trust_env=not request.local_only, follow_redirects=False) as client:
            async with client.stream(
                "POST", normalize_openai_base(request.base_url) + "/chat/completions",
                json=payload,
                headers=_request_headers(request),
            ) as response:
                if response.status_code >= 300:
                    body = await response.aread()
                    if payload.get("tools") and response.status_code in (400, 404, 422, 501):
                        raise ToolsUnsupported(f"LLM HTTPエラー {response.status_code}")
                    raise _http_error(request, response.status_code, body)
                # 道具の呼び出しは断片（index ごとの name / arguments）で流れてくるので、つなげてから返す
                calls: dict[int, dict[str, Any]] = {}
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
                        # 外部のプロバイダ（OpenRouter）は推論を reasoning で流す。
                        reasoning = str(delta.get("reasoning_content") or delta.get("reasoning") or "")
                        content = str(delta.get("content") or "")
                        if reasoning:
                            yield RuntimeChunk("thinking", content=reasoning)
                        if content:
                            yield RuntimeChunk("content", content=content)
                        for part in delta.get("tool_calls") or []:
                            if not isinstance(part, dict):
                                continue
                            entry = calls.setdefault(int(part.get("index") or 0), {"id": "", "name": "", "arguments": ""})
                            entry["id"] = str(part.get("id") or entry["id"])
                            function = part.get("function") or {}
                            entry["name"] += str(function.get("name") or "")
                            arguments = function.get("arguments")
                            entry["arguments"] += arguments if isinstance(arguments, str) else (
                                json.dumps(arguments, ensure_ascii=False) if arguments else "")
                    if isinstance(usage, dict):
                        yield RuntimeChunk("usage", usage=usage)
                if calls:
                    yield RuntimeChunk("tool_call", tool_calls=[
                        {**calls[i], "id": calls[i]["id"] or f"call_{i}"} for i in sorted(calls) if calls[i]["name"]])


class LlamaCppRuntimeProvider(OpenAICompatibleRuntimeProvider):
    kind = "llama.cpp"
    # 2026-11-20 実機（b12388、jinja 既定、Qwen3-4B-Instruct）で、要るときだけ呼び、結果を受けて答えるのを確かめた
    tools_verified = True

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
        from app.models_mgmt import large_model_lifecycle, llama, local_llm
        from urllib.parse import urlsplit as _split

        target = await asyncio.to_thread(
            local_llm.instance_for_port, _split(normalize_openai_base(request.base_url)).port or 0)
        if target is not None:
            await large_model_lifecycle.prepare_switch(str(target["alias"]))
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
        if request.tools:
            return False   # 道具は OpenAI 互換の口（/v1）で扱う（Ollama も tools と tool_calls の流しに対応）
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
        options: dict[str, Any] = {"num_predict": request.max_tokens}
        if request.temperature is not None:
            options["temperature"] = request.temperature
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
            async with httpx.AsyncClient(timeout=request.timeout_seconds, trust_env=not request.local_only, follow_redirects=False) as client:
                response = await client.post(
                    self._native_base(request.base_url) + "/api/chat",
                    json=self._native_payload(request, stream=False, response_format=candidate),
                )
            if response.status_code < 400 or response.status_code not in {400, 404, 415, 422, 501}:
                break
        if response is None:
            raise RuntimeProviderError("LLM応答がありません")
        if response.status_code >= 300:
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
        async with httpx.AsyncClient(timeout=None, trust_env=not request.local_only, follow_redirects=False) as client:
            async with client.stream(
                "POST", self._native_base(request.base_url) + "/api/chat",
                json=self._native_payload(request, stream=True, response_format=request.response_format),
            ) as response:
                if response.status_code >= 300:
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


class StrataRuntimeProvider(OpenAICompatibleRuntimeProvider):
    """Managed Strata OpenAI-compatible server with explicit first activation."""

    kind = "strata"
    # 2026-11-20 実機（Qwen3.8 Flash Next、ゲートウェイ経由）で、挨拶・計算では呼ばず、番組表は資料と Web で、
    # 天気は Web で探して答えるのを確かめた。Maya も別に確かめた
    tools_verified = True

    async def _prepare(self, request: RuntimeChatRequest) -> None:
        await super()._prepare(request)
        from app.models_mgmt import strata_gateway
        from urllib.parse import urlsplit

        port = urlsplit(normalize_openai_base(request.base_url)).port
        instance = next(
            (item for item in await asyncio.to_thread(strata_gateway.list_instances) if item["port"] == port),
            None,
        )
        if instance is None or not await strata_gateway.ensure_ready(
            str(instance["alias"]), timeout_seconds=420
        ):
            raise RuntimeProviderError("Strataモデルが未登録・未起動、または読み込みに失敗しました")

    def _payload(self, request: RuntimeChatRequest, *, stream: bool) -> dict[str, Any]:
        payload = super()._payload(request, stream=stream)
        # Strata exposes its own model ID on its one-model OpenAI endpoint.
        payload["model"] = "strata"
        return payload


class MayaRuntimeProvider(OpenAICompatibleRuntimeProvider):
    """Managed Maya-S. It never truncates: prompt + max_tokens over its context is a 400.

    llama.cpp quietly accepts an oversized max_tokens, so callers size it for the
    model rather than for this server. Count the prompt with Maya's own template
    first and shrink only the output budget to fit.
    """

    kind = "maya-s"
    # 2026-11-20 実機（DS-XS、ゲートウェイ経由・ストリーム）: 道具の呼び出しは DeepSeek の DSML で書かれ、Maya が
    # OpenAI の tool_calls に直して返す（形式の違いは無い）。挨拶 8 回とも呼ばず、天気 8 回とも Web を探した。
    # 最初の確認で挨拶に資料を探した 1 回は、起動直後の最初の要求だけで再現しなかった
    tools_verified = True

    async def _prepare(self, request: RuntimeChatRequest) -> None:
        await super()._prepare(request)
        from app.models_mgmt import maya_s

        # 要求されたモデル（登録名）を起動する。分からなければ動いているもの、それも無ければ最初に登録したもの
        aliases = [str(item["alias"]) for item in await asyncio.to_thread(maya_s.list_instances)]
        alias = request.model if request.model in aliases else await asyncio.to_thread(maya_s.running_alias)
        if not await maya_s.ensure_ready(alias):
            raise RuntimeProviderError("Mayaが起動していないか、読み込み中です。Modelsで起動してください")
        port = next((int(item["port"]) for item in await asyncio.to_thread(maya_s.list_instances)
                     if item["alias"] == alias), maya_s._PORT)
        counted = await maya_s.prompt_room(request.messages, port=port)
        if counted is None:
            # Count unavailable: let the server decide, as before.
            return
        prompt, context = counted
        room = context - prompt
        if room < 1:
            raise RuntimeProviderError(
                f"入力が長すぎます（{prompt}トークン）。Mayaのコンテキストは{context}トークンです。"
                "会話を短くするか、Modelsでコンテキストを広げてください")
        request.max_tokens = min(int(request.max_tokens or room), room)


class RadianceRuntimeProvider(OpenAICompatibleRuntimeProvider):
    """Managed Radiance model: started on request (when allowed), then plain OpenAI chat.

    Radiance cuts an oversized max_tokens to what its context leaves rather than
    refusing it, so only the prompt itself has to fit.
    """

    kind = "radiance"
    # 2026-11-20 実機（Qwen3.8-27B Radiance、ControlDeck のゲートウェイ経由）で、挨拶・お礼・計算では呼ばず、
    # 「さっき調べた番組表で夜 9 時は？」では「神奈川 地上波 番組表 夜 9時」で探して答えるのを確かめた
    tools_verified = True

    async def _prepare(self, request: RuntimeChatRequest) -> None:
        await super()._prepare(request)
        from app.models_mgmt import radiance
        from urllib.parse import urlsplit

        port = urlsplit(normalize_openai_base(request.base_url)).port
        instance = next(
            (item for item in await asyncio.to_thread(radiance.list_instances) if item["port"] == port), None)
        if instance is None or not await radiance.ensure_ready(str(instance["alias"])):
            raise RuntimeProviderError("Radianceモデルが未登録・未起動、または読み込みに失敗しました")
        counted = await radiance.prompt_room(str(instance["alias"]), request.messages)
        if counted is not None and counted[0] >= counted[1]:
            raise RuntimeProviderError(
                f"入力が長すぎます（{counted[0]}トークン）。このモデルのコンテキストは{counted[1]}トークンです。"
                "会話を短くするか、Modelsでコンテキストを広げてください")

    def _payload(self, request: RuntimeChatRequest, *, stream: bool) -> dict[str, Any]:
        payload = super()._payload(request, stream=stream)
        # The server is started with --served-model-name <alias>; any name is served anyway.
        return payload


_OLLAMA = OllamaRuntimeProvider()
_LLAMA = LlamaCppRuntimeProvider()
_STRATA = StrataRuntimeProvider()
_MAYA = MayaRuntimeProvider()
_RADIANCE = RadianceRuntimeProvider()
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
        from app.models_mgmt import llama, strata, strata_gateway

        parsed = urlsplit(normalized)
        if parsed.hostname in ("127.0.0.1", "localhost", "::1"):
            # A registered Strata/Maya-S model owns its port. Check these before
            # llama.cpp: an empty llama endpoint left on the same port would
            # otherwise send a stopped Strata request to llama, which never
            # starts it ("All connection attempts failed").
            if strata.installed() and parsed.port in {int(i["port"]) for i in strata_gateway.list_instances()}:
                return _STRATA
            from app.models_mgmt import maya_s

            if parsed.port in {int(i["port"]) for i in maya_s.list_instances()}:
                return _MAYA
            from app.models_mgmt import radiance

            if radiance.installed() and parsed.port in {int(i["port"]) for i in radiance.list_instances()}:
                return _RADIANCE
            if parsed.port in llama.endpoint_ports():
                return _LLAMA
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


_ALL_PROVIDERS = (_OLLAMA, _LLAMA, _STRATA, _MAYA, _RADIANCE, _OPENAI)


def active_request_count() -> int:
    return sum(provider.active_request_count for provider in _ALL_PROVIDERS)


async def cancel_request(request_id: str) -> bool:
    """provider種別を知らない上位job/APIから生成を明示取消する。"""
    results = await asyncio.gather(*(provider.cancel(request_id) for provider in _ALL_PROVIDERS))
    return any(results)
