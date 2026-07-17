"""ワークフロー Chat / アシスタント API。

- chat/stream: Ollama(OpenAI互換) との対話（ストリーミング WS）
- chat/search: Web(SearXNG/DuckDuckGo)・学術串刺し・Deep サーチをチャットから利用
- chat/generate-workflow: 目的→フロー定義生成（プレビュー用）
- chat/register-workflow: 定義の検証・登録（任意で即実行）
- chat/build WS: 生成→検証→登録→実行→結果確認→LLM 修正の自動ループ

新規ノードは追加せず、既存ノード実装（web.search / academic.search 等）を再利用する。
"""
from __future__ import annotations

import asyncio
import json

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.audit import service as audit
from app.database import SessionLocal, get_db
from app.models import User, Workflow, WorkflowExecution
from app.security.deps import authenticate_websocket, require_permission
from app.workflows import catalog, engine
from app.workflows import external_search as ext
from app.workflows.nodes import NodeError, node_web_search

router = APIRouter(prefix="/chat", tags=["chat"])


def _keep_alive() -> str:
    """モデル常駐時間。設定の default_keep_alive を使い、頻繁な再ロード（大型モデルは
    数十秒のロードが都度発生）を防ぐ。Ollama 以外の OpenAI 互換サーバーは無視するだけ。"""
    try:
        from app.models_mgmt import ollama

        return str(ollama.get_settings().get("default_keep_alive") or "30m")
    except Exception:
        return "30m"


def _native_base(base_url: str) -> str | None:
    """OpenAI 互換 URL(.../v1) から Ollama ネイティブ API のベースを導く。
    think(思考オフ/レベル)は OpenAI 互換 API では効かず、ネイティブ /api でのみ効く。"""
    b = base_url.rstrip("/")
    if b.endswith("/v1"):
        candidate = b[:-3].rstrip("/")
        try:
            from app.models_mgmt import ollama

            if candidate == ollama.base_url().rstrip("/"):
                return candidate
        except Exception:
            pass
    return None


def _think_for(model: str):
    try:
        from app.models_mgmt import ollama

        return ollama.effective_think(model)
    except Exception:
        return None


async def _llm(
    messages: list[dict], base_url: str, model: str, api_key: str,
    temperature: float = 0.4, *, max_tokens: int = 2048,
    disable_thinking: bool = False, response_format: dict | None = None,
    context_window: int | None = None, timeout_seconds: int = 300,
) -> str:
    """OpenAI互換/Ollama共通の有限生成。

    reasoning modelが回答を出さないままcontext上限まで走らないよう、全呼び出しに
    max tokenを設定する。構造化生成ではthinkingを止め、schemaを優先する。
    """
    from app.models_mgmt.runtime_provider import (
        RuntimeChatRequest, RuntimeProviderError, provider_for_base_url,
    )

    think = False if disable_thinking else _think_for(model)
    request = RuntimeChatRequest(
        base_url=base_url, model=model, messages=messages, api_key=api_key,
        temperature=temperature, max_tokens=max_tokens,
        thinking=think, disable_thinking=disable_thinking,
        response_format=response_format, keep_alive=_keep_alive(), context_window=context_window,
        timeout_seconds=timeout_seconds,
    )
    try:
        return await provider_for_base_url(base_url).complete(request)
    except RuntimeProviderError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


# ---- チャット（ストリーミング） ----


@router.websocket("/stream")
async def chat_stream(websocket: WebSocket):
    """Ollama チャットのストリーミング。最初のメッセージ {messages, base_url, model}。"""
    db = SessionLocal()
    try:
        user = await authenticate_websocket(websocket, db, "workflows.run")
        if user is None:
            return
    finally:
        db.close()
    await websocket.accept()
    try:
        first = await asyncio.wait_for(websocket.receive_text(), timeout=15)
        req = json.loads(first)
    except Exception:
        await websocket.close(code=4400)
        return
    base = str(req.get("base_url") or "http://127.0.0.1:11434/v1").rstrip("/")
    model = str(req.get("model") or "llama3.2")
    messages = req.get("messages") or []
    # think: リクエスト指定 > モデル個別設定
    from app.models_mgmt import ollama as _ollama

    think = _ollama.normalize_think(req.get("think")) if req.get("think") is not None else _think_for(model)
    from app.models_mgmt.runtime_provider import (
        GenerationCancelled, RuntimeChatRequest, provider_for_base_url,
    )

    provider = provider_for_base_url(base)
    request_id = f"legacy-ws-{id(websocket)}"
    runtime_request = RuntimeChatRequest(
        base_url=base, model=model, messages=messages, max_tokens=2048,
        thinking=think, disable_thinking=think is None, keep_alive=_keep_alive(),
    )
    try:
        async for chunk in provider.stream_chat(runtime_request, request_id=request_id):
            if chunk.type == "thinking":
                await websocket.send_text(json.dumps({"type": "thinking", "content": chunk.content}))
            elif chunk.type == "content":
                await websocket.send_text(json.dumps({"type": "delta", "content": chunk.content}))
        await websocket.send_text(json.dumps({"type": "done"}))
    except WebSocketDisconnect:
        return
    except GenerationCancelled:
        return
    except Exception as e:
        try:
            await websocket.send_text(json.dumps({"type": "error", "message": f"{type(e).__name__}: {e}"}))
        except Exception:
            pass
    finally:
        await provider.cancel(request_id)
        try:
            await websocket.close()
        except RuntimeError:
            pass


# ---- 検索（チャットのコンテキスト注入用） ----


class SearchBody(BaseModel):
    query: str = Field(min_length=1, max_length=2000)
    mode: str = "web"  # web | academic | deep
    engine: str = "duckduckgo"  # web/deep 用: duckduckgo | searxng
    searxng_url: str = ""
    max_results: int = 8
    # deep 用 LLM
    base_url: str = "http://127.0.0.1:11434/v1"
    model: str = "llama3.2"
    api_key: str = ""


async def _web_results(body: SearchBody, query: str, limit: int) -> list[dict]:
    config = {"query": query, "engine": body.engine, "searxng_url": body.searxng_url, "max_results": limit}
    out = await node_web_search(config, {})
    return out["results"]


async def _page_text(url: str, limit_chars: int = 3500) -> str:
    """公開ページ/PDF本文を有限長で抽出。private addressとredirect SSRFを拒否する。"""
    import ipaddress
    import io
    import socket
    from urllib.parse import urljoin, urlsplit

    from bs4 import BeautifulSoup

    try:
        async def validate_public(target: str) -> None:
            parsed = urlsplit(target)
            if parsed.scheme not in ("http", "https") or not parsed.hostname:
                raise ValueError("HTTP(S)公開URLではありません")
            default_port = 443 if parsed.scheme == "https" else 80
            infos = await asyncio.to_thread(socket.getaddrinfo, parsed.hostname, parsed.port or default_port, type=socket.SOCK_STREAM)
            addresses = {ipaddress.ip_address(info[4][0]) for info in infos}
            if not addresses or any(
                address.is_private or address.is_loopback or address.is_link_local
                or address.is_multicast or address.is_reserved or address.is_unspecified
                for address in addresses
            ):
                raise ValueError("非公開addressは取得できません")

        current = url
        content = b""
        content_type = ""
        async with httpx.AsyncClient(timeout=20, follow_redirects=False,
                                     headers={"User-Agent": "Mozilla/5.0 ControlDeck"}) as client:
            for _ in range(5):
                await validate_public(current)
                async with client.stream("GET", current) as response:
                    if response.status_code in (301, 302, 303, 307, 308) and response.headers.get("location"):
                        current = urljoin(current, response.headers["location"])
                        continue
                    if response.status_code >= 400:
                        return ""
                    content_type = response.headers.get("content-type", "text/html").casefold()
                    chunks: list[bytes] = []
                    size = 0
                    async for chunk in response.aiter_bytes():
                        size += len(chunk)
                        if size > 20 * 1024 * 1024:
                            return ""
                        chunks.append(chunk)
                    content = b"".join(chunks)
                    break
            else:
                return ""
        if "pdf" in content_type or current.casefold().endswith(".pdf"):
            from pypdf import PdfReader

            reader = PdfReader(io.BytesIO(content))
            pages: list[str] = []
            used = 0
            for page in reader.pages[:80]:
                text = page.extract_text() or ""
                pages.append(text)
                used += len(text)
                if used >= limit_chars:
                    break
            return "\n".join(pages)[:limit_chars]
        if "html" not in content_type and "text/" not in content_type:
            return ""
        encoding = "utf-8"
        text_content = content.decode(encoding, errors="replace")
        soup = BeautifulSoup(text_content, "html.parser")
        for tag in soup(["script", "style", "nav", "footer", "header", "aside", "noscript"]):
            tag.decompose()
        text = " ".join(soup.get_text(" ", strip=True).split())
        return text[:limit_chars]
    except Exception:
        return ""


@router.post("/searxng-warmup")
async def searxng_warmup(user: User = Depends(require_permission("workflows.run"))):
    """SearXNG の先読み起動。UI がエンジン選択時に投げ、検索時の待ちをなくす。"""
    from app.workflows import searxng

    url = await searxng.resolve_url("")
    asyncio.get_event_loop().create_task(searxng.ensure_running(url))
    return {"ok": True, "url": url}


@router.post("/search")
async def chat_search(body: SearchBody, user: User = Depends(require_permission("workflows.run"))):
    """チャット用の検索。mode=web/academic は結果一覧、deep は引用付きレポートを返す。"""
    limit = max(1, min(body.max_results, 20))
    if body.mode == "academic":
        fed = await ext.federated(body.query, limit)
        return {"mode": "academic", "results": fed["results"][: limit * 3], "errors": fed["errors"]}

    if body.mode == "web":
        try:
            results = await _web_results(body, body.query, limit)
        except NodeError as e:
            raise HTTPException(status_code=502, detail=str(e))
        return {"mode": "web", "results": results}

    if body.mode != "deep":
        raise HTTPException(status_code=422, detail=f"未知の検索モード: {body.mode}")
    return await _deep_search(body)


async def _deep_search(body: SearchBody, progress=None) -> dict:
    """反復型Deep Research。検索実装とruntimeを共通engineへ注入する。"""
    from app.models_mgmt import runtime_policy
    from app.workflows import deep_research
    from app.workflows.engine import _load_secrets

    settings = runtime_policy.get_policy().deep_research
    secrets = await asyncio.to_thread(_load_secrets)
    context_state = await runtime_policy.prepare_deep_research_context(body.base_url, body.model)
    request_context = context_state.get("request_context_tokens")
    if progress:
        progress(
            "context", f"Deep Research CTX: {context_state.get('reason') or '未適用'}", 0,
            {"context_profile": context_state},
        )

    async def complete(messages: list[dict], *, max_tokens: int, response_format: dict | None = None) -> str:
        return await _llm(
            messages, body.base_url, body.model, body.api_key, temperature=0.25,
            max_tokens=max_tokens, disable_thinking=response_format is not None,
            response_format=response_format, context_window=request_context,
            timeout_seconds=settings.timeout_seconds,
        )

    async def web_search(query: str, limit: int) -> list[dict]:
        return await _web_results(body, query, limit)

    async def academic_search(query: str, limit: int) -> list[dict]:
        result = await ext.federated(query, limit)
        return result.get("results", [])[: max(12, limit * 3)]

    async def specialized_search(source_type: str, query: str, limit: int) -> list[dict]:
        api_key = secrets.get("PATENTSVIEW_API_KEY", "") if source_type == "patent" else ""
        return await ext.search(source_type, query, limit, api_key=api_key)

    try:
        result = await deep_research.run_deep_research(
            body.query, complete=complete, web_search=web_search, academic_search=academic_search,
            specialized_search=specialized_search, page_fetch=_page_text, progress=progress,
            max_rounds=4, max_search_calls=24,
            max_evidence_chars=settings.evidence_context_chars,
            max_report_tokens=settings.max_report_tokens,
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    finally:
        restore_state = await runtime_policy.restore_deep_research_context(context_state)
        context_state.update(restore_state)
        if progress:
            progress(
                "context_restore", f"Deep Research CTX: {restore_state['restore_reason']}", 0,
                {"context_profile": context_state},
            )
    result["research"]["context_profile"] = context_state
    return result


# ---- ワークフロー生成 ----


class GenerateBody(BaseModel):
    goal: str = Field(min_length=1, max_length=4000)
    base_url: str = "http://127.0.0.1:11434/v1"
    model: str = "llama3.2"
    api_key: str = ""
    name: str = ""


GEN_SYSTEM = """あなたは Control Deck のワークフロー設計器です。ユーザーの目的から、
実行可能なワークフロー定義を **JSON のみ** で出力します。形式:
{"name":"...", "nodes":[{"id":"n1","type":"...","name":"...","config":{...}}],
 "edges":[{"source":"n1","target":"n2","branch":null}]}

規則:
- 必ず trigger ノードを1つだけ含める（id は "trigger" 推奨、config は {"mode":"manual"}）
- 使用できる type は以下のノード一覧のみ。存在しない type は使わない
- テンプレートは {{ノードID.フィールド}} で前段出力を参照
- 分岐ノードは edges の branch に "true"/"false"、ループは "body"/"done" を指定
- チャット応答/結果表示には signal.display ノードを最後に置く
- LLM を使うノードの base_url/model は {base_url} / {model} を設定
- 余計な説明は書かず JSON だけを返す

利用可能ノード:
{catalog}
"""

WORKFLOW_SCHEMA = {
    "type": "object",
    "required": ["name", "nodes", "edges"],
    "properties": {
        "name": {"type": "string"},
        "nodes": {
            "type": "array",
            "items": {
                "type": "object", "required": ["id", "type", "config"],
                "properties": {
                    "id": {"type": "string"}, "type": {"type": "string"},
                    "name": {"type": "string"}, "config": {"type": "object"},
                },
            },
        },
        "edges": {
            "type": "array",
            "items": {
                "type": "object", "required": ["source", "target"],
                "properties": {
                    "source": {"type": "string"}, "target": {"type": "string"},
                    "branch": {"type": ["string", "null"]},
                },
            },
        },
    },
}


def _gen_system(base_url: str, model: str) -> str:
    return (GEN_SYSTEM
            .replace("{catalog}", catalog.catalog_prompt())
            .replace("{base_url}", base_url)
            .replace("{model}", model))


def _extract_json(text: str) -> dict:
    """説明文やcode fenceが混じっても、最初の完全なJSON objectを取り出す。"""
    decoder = json.JSONDecoder()
    for index, char in enumerate(text):
        if char != "{":
            continue
        try:
            value, _ = decoder.raw_decode(text[index:])
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            return value
    raise ValueError("LLM が完全な JSON object を返しませんでした")


def _workflow_max_tokens() -> int:
    """Model画面の共通出力上限を使う。schema生成には最低4Kを確保する。"""
    try:
        from app.models_mgmt.runtime_policy import get_policy

        configured = get_policy().chat.max_output_tokens
    except Exception:
        configured = 8192
    return min(131072, max(4096, int(configured)))


def _validate_generated(definition: dict) -> list[str]:
    """未知タイプ + 構造検証 + 意味検証（エラーのみ）。問題のリストを返す（空なら OK）。"""
    from app.workflows.validation import semantic_check

    problems: list[str] = []
    unknown = sorted({str(n.get("type")) for n in definition.get("nodes", [])} - catalog.valid_types())
    if unknown:
        problems.append(f"存在しないノード type が使われています: {', '.join(unknown)}")
    try:
        engine.validate_definition(json.dumps(definition))
    except engine.DefinitionError as e:
        problems.append(str(e))
    if not problems:  # 構造 OK のときのみ意味検証（エラーだけ修正対象に）
        errors, _ = semantic_check(definition.get("nodes", []), definition.get("edges", []))
        problems.extend(errors)
    return problems


def _quality(definition: dict, run_ok: bool | None = None) -> dict:
    from app.workflows.validation import quality_score

    return quality_score(definition.get("nodes", []), definition.get("edges", []), run_ok)


@router.post("/generate-workflow")
async def generate_workflow(body: GenerateBody, user: User = Depends(require_permission("workflows.edit"))):
    """目的からワークフロー定義を生成して検証結果とともに返す（登録は別 API）。"""
    content = await _llm(
        [{"role": "system", "content": _gen_system(body.base_url, body.model)},
         {"role": "user", "content": body.goal}],
        body.base_url, body.model, body.api_key, temperature=0.2,
        max_tokens=_workflow_max_tokens(), disable_thinking=True,
        response_format={"type": "json_schema", "schema": WORKFLOW_SCHEMA},
    )
    try:
        data = _extract_json(content)
    except (ValueError, json.JSONDecodeError) as e:
        raise HTTPException(status_code=422, detail=f"生成 JSON の解析に失敗: {e}")
    definition = {"nodes": data.get("nodes", []), "edges": data.get("edges", [])}
    warnings = _validate_generated(definition)
    quality = _quality(definition)
    return {
        "name": body.name or data.get("name", "生成ワークフロー"),
        "definition": definition,
        "valid": not warnings,
        "warnings": warnings + quality["warnings"],
        "quality": quality,
    }


class RegisterBody(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    definition: dict
    run: bool = False


@router.post("/register-workflow", status_code=201)
async def register_workflow(
    body: RegisterBody,
    request: Request,
    user: User = Depends(require_permission("workflows.edit")),
    db: Session = Depends(get_db),
):
    """生成/編集した定義をワークフローとして登録する。run=true で即実行（動作確認）。"""
    definition = json.dumps(body.definition, ensure_ascii=False)
    try:
        engine.validate_definition(definition)
    except engine.DefinitionError as e:
        raise HTTPException(status_code=422, detail=str(e))
    wf = Workflow(name=body.name, description="AI アシスタント生成", definition_json=definition, created_by=user.id)
    db.add(wf)
    db.commit()
    wf_id, wf_name = wf.id, wf.name
    audit.record(db, "workflow.generate", user=user, resource_type="workflow", resource_id=str(wf_id), request=request, metadata={"name": wf_name})
    result: dict = {"id": wf_id, "name": wf_name}
    if body.run:
        try:
            result["execution_id"] = await engine.run_workflow(wf_id, trigger_type="chat-gen")
        except Exception as e:
            result["run_error"] = str(e)[:200]
    return result


# ---- 自動ビルド（生成→検証→登録→実行→確認→修正のループ） ----

BUILD_MAX_ATTEMPTS = 4
BUILD_RUN_TIMEOUT = 180  # 動作確認 1 回あたりの待ち上限（秒）


def _db_call(fn):
    """SessionLocal を開いて fn(db) を実行するヘルパー（スレッドで呼ぶ）。"""
    db = SessionLocal()
    try:
        return fn(db)
    finally:
        db.close()


def _failure_summary(context: dict, error: str) -> str:
    """実行コンテキストから失敗ノードの情報を抽出して LLM 修正用の説明を作る。"""
    lines = [f"実行エラー: {error}"] if error else []
    for node_id, entry in context.items():
        if not isinstance(entry, dict) or node_id.startswith("__"):
            continue
        if entry.get("status") in ("FAILED", "TIMED_OUT"):
            lines.append(f"ノード {node_id}: {entry.get('status')} — {entry.get('error', '')}")
    return "\n".join(lines) or "詳細不明の失敗"


async def _run_build_job(job, req: dict, user_id: int) -> dict:
    """自動ビルド本体（サーバー側ジョブ。ブラウザを閉じても継続する）。"""

    async def emit(payload: dict) -> None:
        job.emit(payload)
        if payload.get("type") == "phase":
            job.set_progress(str(payload.get("phase", "")))

    goal = str(req.get("goal", "")).strip()
    base_url = str(req.get("base_url") or "http://127.0.0.1:11434/v1")
    model = str(req.get("model") or "llama3.2")
    api_key = str(req.get("api_key") or "")
    name = str(req.get("name") or "").strip()
    run_check = bool(req.get("run_check", True))
    definition: dict | None = req.get("definition") or None

    history: list[dict] = [
        {"role": "system", "content": _gen_system(base_url, model)},
        {"role": "user", "content": goal or "以下の定義を修正してください"},
    ]
    wf_id: int | None = None
    exec_id: int | None = None
    status = "FAILED"

    if True:  # 旧 WS 実装との差分を最小にするためのブロック
        for attempt in range(1, BUILD_MAX_ATTEMPTS + 1):
            # 1. 生成（definition が与えられた最初の周は省略）
            if definition is None:
                await emit({"type": "phase", "phase": "generate", "attempt": attempt})
                try:
                    content = await _llm(
                        history, base_url, model, api_key, temperature=0.2,
                        max_tokens=_workflow_max_tokens(), disable_thinking=True,
                        response_format={"type": "json_schema", "schema": WORKFLOW_SCHEMA},
                    )
                except HTTPException as e:
                    await emit({"type": "error", "message": e.detail})
                    return {"status": "FAILED", "error": str(e.detail)}
                try:
                    data = _extract_json(content)
                except (ValueError, json.JSONDecodeError) as e:
                    await emit({"type": "log", "message": f"JSON 解析失敗: {e} — 再生成します"})
                    history += [{"role": "assistant", "content": content},
                                {"role": "user", "content": f"JSON の解析に失敗しました（{e}）。正しい JSON のみを返してください。"}]
                    continue
                definition = {"nodes": data.get("nodes", []), "edges": data.get("edges", [])}
                name = name or str(data.get("name", "") or "生成ワークフロー")

            # 2. 検証
            await emit({"type": "phase", "phase": "validate", "attempt": attempt})
            problems = _validate_generated(definition)
            if problems:
                await emit({"type": "log", "message": "検証 NG: " + " / ".join(problems)})
                history += [{"role": "assistant", "content": json.dumps(definition, ensure_ascii=False)},
                            {"role": "user", "content": "検証エラーです。修正した JSON のみを返してください:\n" + "\n".join(problems)}]
                definition = None
                continue
            await emit({"type": "log", "message": f"検証 OK（ノード {len(definition['nodes'])} 個）",
                        "definition": definition, "name": name})

            # 3. 登録 or 定義更新
            def_json = json.dumps(definition, ensure_ascii=False)
            if wf_id is None:
                def create(db: Session) -> int:
                    wf = Workflow(name=name or "生成ワークフロー", description=f"AI 自動構築: {goal[:180]}",
                                  definition_json=def_json, created_by=user_id)
                    db.add(wf)
                    db.commit()
                    audit.record(db, "workflow.generate", resource_type="workflow",
                                 resource_id=str(wf.id), metadata={"name": wf.name, "auto_build": True})
                    return wf.id

                wf_id = await asyncio.to_thread(_db_call, create)
                await emit({"type": "phase", "phase": "register", "workflow_id": wf_id})
            else:
                def update(db: Session) -> None:
                    wf = db.get(Workflow, wf_id)
                    if wf is not None:
                        wf.definition_json = def_json
                        db.commit()

                await asyncio.to_thread(_db_call, update)
                await emit({"type": "log", "message": f"修正した定義でワークフロー #{wf_id} を更新しました"})

            if not run_check:
                status = "REGISTERED"
                break

            # 4. 実行（動作確認）
            await emit({"type": "phase", "phase": "run", "attempt": attempt, "workflow_id": wf_id})
            exec_id = await engine.run_workflow(wf_id, trigger_type="chat-build", input_data={"message": goal})

            deadline = asyncio.get_event_loop().time() + BUILD_RUN_TIMEOUT
            row_status, row_error, context = "RUNNING", "", {}
            while asyncio.get_event_loop().time() < deadline:
                await asyncio.sleep(1.5)

                def fetch(db: Session) -> tuple[str, str, str]:
                    r = db.get(WorkflowExecution, exec_id)
                    return (r.status, r.error or "", r.context_json or "{}") if r else ("FAILED", "実行レコード消失", "{}")

                row_status, row_error, ctx_json = await asyncio.to_thread(_db_call, fetch)
                if row_status != "RUNNING":
                    context = json.loads(ctx_json)
                    break
            else:
                engine.cancel_execution(exec_id)
                row_status, row_error = "TIMED_OUT", f"動作確認が {BUILD_RUN_TIMEOUT} 秒以内に終わりませんでした"

            await emit({"type": "phase", "phase": "check", "status": row_status, "execution_id": exec_id})
            if row_status == "SUCCEEDED":
                status = "SUCCEEDED"
                break

            # 5. 失敗 → LLM に修正させて再試行
            summary = _failure_summary(context, row_error)
            await emit({"type": "log", "message": f"動作確認 NG:\n{summary}"})
            if attempt >= BUILD_MAX_ATTEMPTS:
                break
            history += [{"role": "assistant", "content": json.dumps(definition, ensure_ascii=False)},
                        {"role": "user", "content":
                         f"実行したところ失敗しました。原因を直した完全な JSON のみを返してください。\n{summary}"}]
            definition = None

        # 品質スコア（実動作の成否を反映）
        run_ok = True if status == "SUCCEEDED" else (False if run_check else None)
        quality = _quality(definition, run_ok) if definition else None
        await emit({"type": "done", "status": status, "workflow_id": wf_id,
                    "execution_id": exec_id, "name": name, "quality": quality})
    return {"status": status, "workflow_id": wf_id, "execution_id": exec_id, "name": name}


@router.websocket("/build")
async def build_workflow_stream(websocket: WebSocket):
    """自然言語の目的からワークフローを自動構築する（サーバー側ジョブ + ストリーム）。

    受信(最初の1メッセージ):
      {goal, base_url, model, api_key?, name?, definition?, run_check?} で新規開始、
      または {job_id} で切断後の再接続。
    送信イベント: {type: job|phase|log|done|error, ...}
    ビルド本体はジョブとして走るため、WS を閉じても処理は継続する。
    """
    from app.jobs import service as jobs_svc

    db = SessionLocal()
    try:
        user = await authenticate_websocket(websocket, db, "workflows.edit")
        if user is None:
            return
        user_id = user.id
    finally:
        db.close()
    await websocket.accept()
    try:
        first = await asyncio.wait_for(websocket.receive_text(), timeout=20)
        req = json.loads(first)
    except Exception:
        await websocket.close(code=4400)
        return

    job = None
    if req.get("job_id"):
        job = jobs_svc.get(str(req["job_id"]))
        if job is None or job.kind != "workflow.build" or not jobs_svc.visible_to(job, user_id):
            await websocket.send_text(json.dumps({"type": "error", "message": "ジョブが見つかりません"}))
            await websocket.close()
            return
    else:
        goal = str(req.get("goal", "")).strip()
        if not goal and not req.get("definition"):
            await websocket.send_text(json.dumps({"type": "error", "message": "goal か definition が必要です"}))
            await websocket.close()
            return
        job = jobs_svc.create("workflow.build", f"自動ビルド: {goal[:60]}",
                              lambda j: _run_build_job(j, req, user_id), owner_user_id=user_id,
                              idempotency_key=str(req.get("idempotency_key") or "") or None, priority=5)

    # ジョブのイベントをストリーム（切断してもジョブは続く。job_id で再接続可能）
    try:
        await websocket.send_text(json.dumps({"type": "job", "job_id": job.id}, ensure_ascii=False))
        cursor = job.event_offset
        while True:
            await jobs_svc.wait_events(job, cursor)
            events, next_cursor, truncated = job.events_since(cursor)
            if truncated:
                await websocket.send_text(json.dumps({
                    "type": "log", "message": "一部の詳細ログを省略し、最新状態へ追いつきました",
                }, ensure_ascii=False))
            for ev in events:
                await websocket.send_text(json.dumps(ev, ensure_ascii=False))
            cursor = next_cursor
            if job.status not in ("queued", "running") and cursor >= job.event_sequence:
                if job.status == "failed":
                    await websocket.send_text(json.dumps({"type": "error", "message": job.error}, ensure_ascii=False))
                break
    except WebSocketDisconnect:
        return
    except Exception:
        pass
    finally:
        try:
            await websocket.close()
        except RuntimeError:
            pass
