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
) -> str:
    """OpenAI互換/Ollama共通の有限生成。

    reasoning modelが回答を出さないままcontext上限まで走らないよう、全呼び出しに
    max tokenを設定する。構造化生成ではthinkingを止め、schemaを優先する。
    """
    from app.models_mgmt.runtime_policy import ensure_gpu_profile

    try:
        await asyncio.to_thread(ensure_gpu_profile, base_url=base_url)
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    think = False if disable_thinking else _think_for(model)
    native = _native_base(base_url) if think is not None else None
    if native is not None and response_format is None:
        # think 指定あり & Ollama → ネイティブ /api/chat（think が効く）
        async with httpx.AsyncClient(timeout=300) as client:
            r = await client.post(
                native + "/api/chat",
                json={"model": model, "messages": messages, "stream": False,
                      "think": think, "keep_alive": _keep_alive(),
                      "options": {"temperature": temperature, "num_predict": max_tokens}},
            )
        if r.status_code >= 400:
            raise HTTPException(status_code=502, detail=f"LLM エラー {r.status_code}: {r.text[:150]}")
        return r.json().get("message", {}).get("content", "")
    payload: dict = {
        "model": model, "messages": messages, "temperature": temperature,
        "stream": False, "keep_alive": _keep_alive(), "max_tokens": max_tokens,
    }
    if disable_thinking:
        payload["chat_template_kwargs"] = {"enable_thinking": False}
    if response_format is not None:
        payload["response_format"] = response_format

    async def call(body: dict) -> httpx.Response:
        async with httpx.AsyncClient(timeout=300) as client:
            return await client.post(
                base_url.rstrip("/") + "/chat/completions", json=body,
                headers={"Authorization": f"Bearer {api_key or 'sk-no-key'}"},
            )

    r = await call(payload)
    if r.status_code >= 400 and response_format is not None:
        # providerごとの差を吸収: llama.cpp形式 → OpenAI標準形式 → prompt制約。
        standard = dict(payload)
        standard["response_format"] = {
            "type": "json_schema",
            "json_schema": {"name": "workflow", "schema": response_format["schema"], "strict": True},
        }
        r = await call(standard)
        if r.status_code >= 400:
            fallback = dict(payload)
            fallback.pop("response_format", None)
            r = await call(fallback)
    if r.status_code >= 400:
        raise HTTPException(status_code=502, detail=f"LLM エラー {r.status_code}: {r.text[:150]}")
    message = r.json()["choices"][0]["message"]
    return message.get("content", "")


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
    native = _native_base(base) if think is not None else None
    try:
        from app.models_mgmt.runtime_policy import ensure_gpu_profile

        await asyncio.to_thread(ensure_gpu_profile, base_url=base)
        if native is not None:
            # think 指定 & Ollama → ネイティブ /api/chat ストリーム（JSON lines）。
            # thinking(推論)は type:"thinking"、回答は type:"delta" で送る
            async with httpx.AsyncClient(timeout=None) as client:
                async with client.stream(
                    "POST", native + "/api/chat",
                    json={"model": model, "messages": messages, "stream": True,
                          "think": think, "keep_alive": _keep_alive()},
                ) as r:
                    if r.status_code >= 400:
                        body = await r.aread()
                        await websocket.send_text(json.dumps({"type": "error", "message": f"{r.status_code}: {body[:150]!r}"}))
                        await websocket.close()
                        return
                    async for line in r.aiter_lines():
                        if not line.strip():
                            continue
                        try:
                            msg = json.loads(line).get("message", {})
                        except json.JSONDecodeError:
                            continue
                        if msg.get("thinking"):
                            await websocket.send_text(json.dumps({"type": "thinking", "content": msg["thinking"]}))
                        if msg.get("content"):
                            await websocket.send_text(json.dumps({"type": "delta", "content": msg["content"]}))
            await websocket.send_text(json.dumps({"type": "done"}))
            return
        async with httpx.AsyncClient(timeout=None) as client:
            async with client.stream(
                "POST", base + "/chat/completions",
            json={"model": model, "messages": messages, "stream": True,
                  "keep_alive": _keep_alive(), "max_tokens": 2048,
                  "chat_template_kwargs": {"enable_thinking": False}},
                headers={"Authorization": "Bearer sk-no-key"},
            ) as r:
                if r.status_code >= 400:
                    body = await r.aread()
                    await websocket.send_text(json.dumps({"type": "error", "message": f"{r.status_code}: {body[:150]!r}"}))
                    await websocket.close()
                    return
                async for line in r.aiter_lines():
                    if not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    if data == "[DONE]":
                        break
                    try:
                        item = json.loads(data)["choices"][0]["delta"]
                        reasoning = item.get("reasoning_content", "")
                        delta = item.get("content", "")
                    except (json.JSONDecodeError, KeyError, IndexError):
                        continue
                    if reasoning:
                        await websocket.send_text(json.dumps({"type": "thinking", "content": reasoning}))
                    if delta:
                        await websocket.send_text(json.dumps({"type": "delta", "content": delta}))
        await websocket.send_text(json.dumps({"type": "done"}))
    except WebSocketDisconnect:
        return
    except Exception as e:
        try:
            await websocket.send_text(json.dumps({"type": "error", "message": f"{type(e).__name__}: {e}"}))
        except Exception:
            pass
    finally:
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
    """ページ本文をテキスト抽出（Deep サーチの引用元）。失敗時は空文字。"""
    from bs4 import BeautifulSoup

    try:
        async with httpx.AsyncClient(timeout=15, follow_redirects=True,
                                     headers={"User-Agent": "Mozilla/5.0 ControlDeck"}) as client:
            r = await client.get(url)
        if r.status_code >= 400 or "text/html" not in r.headers.get("content-type", "text/html"):
            return ""
        soup = BeautifulSoup(r.text, "html.parser")
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


async def _deep_search(body: SearchBody) -> dict:
    """Deep サーチ: サブ質問分解 → Web 検索 → 本文収集 → 引用付き統合レポート。"""
    try:
        raw = await _llm(
            [{"role": "user", "content":
              f"調査テーマ「{body.query}」を Web で調べるための検索クエリを 3 個、1行1個・番号なしで出力。"}],
            body.base_url, body.model, body.api_key, temperature=0.3)
        sub_qs = [ln.strip("・-•*0123456789. \t") for ln in raw.splitlines() if ln.strip()][:3] or [body.query]
    except HTTPException:
        sub_qs = [body.query]

    seen: set[str] = set()
    candidates: list[dict] = []
    for q in sub_qs:
        try:
            for it in await _web_results(body, q, 6):
                if it["url"] and it["url"] not in seen:
                    seen.add(it["url"])
                    candidates.append(it)
        except NodeError:
            continue
    if not candidates:
        raise HTTPException(status_code=502, detail="Web 検索結果が得られませんでした")

    top = candidates[:8]
    texts = await asyncio.gather(*(_page_text(x["url"]) for x in top))
    sources = []
    corpus = []
    for item, text in zip(top, texts):
        content = text or item.get("snippet", "")
        if not content:
            continue
        sources.append({"n": len(sources) + 1, "title": item["title"], "url": item["url"]})
        corpus.append(f"[{len(sources)}] {item['title']}\n{content}")

    report = await _llm(
        [{"role": "system", "content":
          "あなたはリサーチアシスタントです。与えられた出典のみを根拠に、日本語で構造化されたレポートを"
          "Markdown で書いてください。本文中の主張には必ず [番号] で出典を付け、末尾に出典一覧は書かないこと。"},
         {"role": "user", "content": f"テーマ: {body.query}\n\n出典:\n\n" + "\n\n---\n\n".join(corpus)}],
        body.base_url, body.model, body.api_key, temperature=0.3)
    return {"mode": "deep", "report": report, "sources": sources, "sub_questions": sub_qs}


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
    import re

    m = re.search(r"\{.*\}", text, re.DOTALL)
    if not m:
        raise ValueError("LLM が有効な JSON を返しませんでした")
    return json.loads(m.group(0))


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
        max_tokens=800, disable_thinking=True,
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
                        max_tokens=800, disable_thinking=True,
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
        idx = 0
        while True:
            new_len = await jobs_svc.wait_events(job, idx)
            for ev in job.events[idx:new_len]:
                await websocket.send_text(json.dumps(ev, ensure_ascii=False))
            idx = new_len
            if job.status not in ("queued", "running") and idx >= len(job.events):
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
