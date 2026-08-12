"""配布アプリ内でWorkflowを実行する携帯版DAGエンジン。

Control Deck本体の`app/workflows/nodes.py`をそのまま同梱して呼び出すため、
ノードの意味論は本体と常に一致する。ここが持つのはDB・承認・ライブ表示を除いた
実行制御（発火・分岐・dead伝播・join・retry・timeout・loop）だけ。
"""
from __future__ import annotations

import asyncio
import json
import os
import time
from typing import Any, Callable

from app.workflows.contracts import final_outputs
from app.workflows.nodes import (
    DEFAULT_NODE_TIMEOUT,
    NODE_EXECUTORS,
    NODE_TIMEOUTS,
    NodeError,
    render_template,
)
from app.workflows import nodes as workflow_nodes

MAX_PARALLEL_NODES = 4
MAX_STEPS = 500
MAX_LOOP_ITEMS = 100
EXECUTION_TIMEOUT = 3600.0
BRANCHING_TYPES = {"condition.if", "control.try", "control.circuit_breaker", "control.loop"}


class FlowError(RuntimeError):
    pass


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime())


def _edge_branch(edge: dict[str, Any]) -> str:
    return str(edge.get("branch") or edge.get("label") or edge.get("sourceHandle") or "")


async def _portable_llm_chat(config: dict, ctx: dict) -> dict:
    """OpenAI互換endpointへ直接問い合わせる配布版llm.chat。

    本体のllm.chatはGPU profileやモデル常駐をControl Deckのruntimeへ依頼するが、
    配布アプリは外部endpointだけを使う。環境変数が最優先。
    """
    import httpx

    base_url = (
        os.environ.get("FLOW_APP_LLM_BASE_URL")
        or render_template(str(config.get("base_url") or ""), ctx).strip()
    ).rstrip("/")
    model = os.environ.get("FLOW_APP_LLM_MODEL") or render_template(str(config.get("model") or ""), ctx).strip()
    api_key = os.environ.get("FLOW_APP_LLM_API_KEY") or str(config.get("api_key") or "sk-no-key")
    if not base_url.startswith(("http://", "https://")) or not model:
        raise NodeError(
            "LLMの接続先が未設定です。FLOW_APP_LLM_BASE_URL と FLOW_APP_LLM_MODEL を指定してください",
            code="LLM_ROUTE_INVALID", retryable=False,
        )
    prompt = render_template(str(config.get("prompt", "")), ctx)
    system = render_template(str(config.get("system", "")), ctx)
    messages = ([{"role": "system", "content": system}] if system else []) + [{"role": "user", "content": prompt}]
    payload: dict[str, Any] = {
        "model": model, "messages": messages, "stream": False,
        "temperature": float(config.get("temperature", 0.7) or 0),
        "max_tokens": int(config.get("max_tokens") or 2048),
    }
    response_format = str(config.get("response_format", "") or "")
    if response_format == "json_object":
        payload["response_format"] = {"type": "json_object"}
    timeout = max(5.0, min(float(config.get("node_timeout") or 300), 900.0))
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            reply = await client.post(
                f"{base_url}/chat/completions", json=payload,
                headers={"Authorization": f"Bearer {api_key}"},
            )
    except httpx.HTTPError as exc:
        raise NodeError(f"LLM 接続失敗: {exc}") from exc
    if reply.status_code >= 400:
        raise NodeError(f"LLM エラー {reply.status_code}: {reply.text[:200]}")
    try:
        data = reply.json()
        content = data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, ValueError) as exc:
        raise NodeError("LLM 応答の解析に失敗しました") from exc
    out: dict[str, Any] = {"content": content, "model": model, "tokens": (data.get("usage") or {}).get("total_tokens")}
    if response_format in {"json_object", "json_schema"}:
        text = content.strip()
        if text.startswith("```"):
            text = text.split("\n", 1)[-1].rsplit("```", 1)[0]
        try:
            out["json"] = json.loads(text)
        except json.JSONDecodeError:
            out["json"] = None
            out["json_error"] = "応答を JSON として解析できませんでした"
    return out


def executors() -> dict[str, Any]:
    """配布アプリで使える実行関数表（本体の実装＋配布版のLLM）。"""
    table = dict(NODE_EXECUTORS)
    table["llm.chat"] = _portable_llm_chat
    return table


class FlowRunner:
    def __init__(self, definition: dict[str, Any], on_event: Callable[[dict[str, Any]], None] | None = None):
        self.nodes: list[dict[str, Any]] = list(definition.get("nodes") or [])
        self.edges: list[dict[str, Any]] = list(definition.get("edges") or [])
        self.node_by_id = {str(node["id"]): node for node in self.nodes if node.get("id")}
        self.outgoing: dict[str, list[dict[str, Any]]] = {}
        for edge in self.edges:
            self.outgoing.setdefault(str(edge.get("source")), []).append(edge)
        self.executors = executors()
        self.on_event = on_event
        self.steps = 0
        self.semaphore = asyncio.Semaphore(MAX_PARALLEL_NODES)

    def emit(self, kind: str, **payload: Any) -> None:
        if self.on_event is not None:
            self.on_event({"kind": kind, **payload})

    async def run(self, input_data: dict[str, Any]) -> dict[str, Any]:
        trigger = next((node for node in self.nodes if node.get("type") == "trigger"), None)
        if trigger is None:
            raise FlowError("トリガーノードがありません")
        context: dict[str, Any] = {"__input__": dict(input_data or {}), "__vars__": {}, "__secrets__": {}}
        started = time.monotonic()
        status, error = "SUCCEEDED", ""
        try:
            async with asyncio.TaskGroup() as group:
                run = _DagRun(self, group, context)
                await run.start(str(trigger["id"]))
        except* Exception as failures:  # noqa: B036 - 失敗理由をそのまま要約する
            status = "FAILED"
            error = "; ".join(str(item) for item in failures.exceptions)[:500]
        return {
            "status": status,
            "error": error,
            "elapsedMs": int((time.monotonic() - started) * 1000),
            "outputs": final_outputs(context, expose_source=False),
            "nodes": [
                {
                    "id": node_id, "name": entry.get("name") or node_id, "type": entry.get("type") or "",
                    "status": entry.get("status") or "", "error": entry.get("error") or "",
                    "output": entry.get("output"),
                }
                for node_id, entry in context.items()
                if not node_id.startswith("__") and isinstance(entry, dict)
            ],
        }

    async def run_single(self, node: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
        node_id, node_type = str(node["id"]), str(node.get("type") or "")
        config = dict(node.get("config") or {})
        self.steps += 1
        if self.steps > MAX_STEPS:
            raise FlowError(f"ステップ数が上限（{MAX_STEPS}）を超えました")
        executor = self.executors.get(node_type)
        entry: dict[str, Any] = {"status": "PENDING", "name": node.get("name") or node_id, "type": node_type}
        context[node_id] = entry
        if executor is None:
            entry.update(status="FAILED", error=f"この配布アプリでは実行できないノードです: {node_type}")
            self.emit("node", id=node_id, status="FAILED", name=entry["name"])
            raise FlowError(entry["error"])
        if bool(node.get("disabled")) and node_type != "trigger":
            entry.update(status="SKIPPED", output={"disabled": True, "skipped": True}, finished_at=_now())
            return entry

        retries = max(0, min(int(config.get("retry_count", 0) or 0), 5))
        retry_wait = max(0.0, min(float(config.get("retry_wait", 5) or 5), 300.0))
        default_timeout = NODE_TIMEOUTS.get(node_type, DEFAULT_NODE_TIMEOUT)
        try:
            timeout = max(0.1, min(float(config.get("node_timeout") or default_timeout), EXECUTION_TIMEOUT))
        except (TypeError, ValueError):
            timeout = float(default_timeout)
        entry.update(status="RUNNING", started_at=_now())
        self.emit("node", id=node_id, status="RUNNING", name=entry["name"])
        attempt = 0
        while True:
            attempt += 1
            retryable = True
            token = workflow_nodes._progress_reporter.set(
                lambda message, current=0, total=0: entry.update(
                    progress={"message": str(message)[:200], "current": int(current), "total": int(total)}
                )
            )
            try:
                effective = {**config, "__pause_response": {}, "__workflow_id": 0, "__execution_id": None, "__node_id": node_id}
                async with self.semaphore:
                    output = await asyncio.wait_for(executor(effective, context), timeout=timeout)
                entry.update(status="SUCCEEDED", output=output, finished_at=_now(), attempts=attempt)
                variable = str(config.get("output_var") or "").strip()
                if variable:
                    context.setdefault("__vars__", {})[variable] = output
                self.emit("node", id=node_id, status="SUCCEEDED", name=entry["name"])
                return entry
            except asyncio.TimeoutError:
                message, final_status = "タイムアウト", "TIMED_OUT"
            except NodeError as exc:
                message, final_status, retryable = str(exc), "FAILED", exc.retryable
            except Exception as exc:  # noqa: BLE001 - 想定外もリトライ対象にする
                message, final_status = f"{type(exc).__name__}: {exc}", "FAILED"
            finally:
                workflow_nodes._progress_reporter.reset(token)
            if attempt <= retries and retryable:
                entry.update(status="RETRYING", error=message, attempts=attempt)
                await asyncio.sleep(retry_wait)
                entry["status"] = "RUNNING"
                continue
            entry.update(status=final_status, error=message, output={"error": message}, finished_at=_now(), attempts=attempt)
            self.emit("node", id=node_id, status=final_status, name=entry["name"], error=message)
            if str(config.get("on_error", "stop")) == "stop":
                raise FlowError(f"ノード {entry['name']} が失敗しました: {message}")
            return entry


class _DagRun:
    """発火カウント方式のDAG実行状態（本体engine v2と同じ判定規則）。"""

    def __init__(self, runner: FlowRunner, group: asyncio.TaskGroup, context: dict[str, Any]):
        self.runner = runner
        self.group = group
        self.context = context
        self.lock = asyncio.Lock()
        self.received: dict[str, int] = {}
        self.live_received: dict[str, int] = {}
        self.arrivals: dict[str, list[str]] = {}
        self.successful: dict[str, list[str]] = {}
        self.ran: set[str] = set()
        self.incoming = {node_id: 0 for node_id in runner.node_by_id}
        self.incoming_sources: dict[str, list[str]] = {node_id: [] for node_id in runner.node_by_id}
        for edge in runner.edges:
            target = str(edge.get("target"))
            self.incoming[target] = self.incoming.get(target, 0) + 1
            self.incoming_sources.setdefault(target, []).append(str(edge.get("source")))

    async def start(self, node_id: str) -> None:
        async with self.lock:
            if node_id in self.ran:
                return
            self.ran.add(node_id)
        self.group.create_task(self.exec_node(node_id))

    async def fire(self, target: str, live: bool, source: str | None = None) -> None:
        node = self.runner.node_by_id.get(target)
        if node is None:
            return
        config = node.get("config") or {}
        merge_mode = str(config.get("mode") or "wait_all") if node.get("type") == "control.merge" else ""
        join_all = str(config.get("join", "")) == "all" or merge_mode in {"wait_all", "collect"}
        async with self.lock:
            self.received[target] = self.received.get(target, 0) + 1
            if live:
                self.live_received[target] = self.live_received.get(target, 0) + 1
                if source and source not in self.arrivals.setdefault(target, []):
                    self.arrivals[target].append(source)
                source_entry = self.context.get(source or "")
                if source and isinstance(source_entry, dict) and source_entry.get("status") == "SUCCEEDED":
                    if source not in self.successful.setdefault(target, []):
                        self.successful[target].append(source)
            if target in self.ran:
                return
            resolved = self.received[target] >= self.incoming.get(target, 0)
            lives = self.live_received.get(target, 0)
            successes = len(self.successful.get(target, []))
            if merge_mode == "first_success":
                run = successes >= 1 or resolved
                if not run:
                    return
            elif merge_mode == "quorum":
                quorum = max(1, min(int(config.get("quorum") or 1), max(1, self.incoming.get(target, 1))))
                run = successes >= quorum or resolved
                if not run:
                    return
            elif join_all:
                if not resolved:
                    return
                run = lives > 0
            else:
                run = live
                if not run and not (resolved and lives == 0):
                    return
            if run:
                self.ran.add(target)
        if run:
            self.group.create_task(self.exec_node(target))
        else:
            self.context.setdefault(target, {"status": "SKIPPED"})
            for edge in self.runner.outgoing.get(target, []):
                await self.fire(str(edge.get("target")), live=False, source=target)

    async def exec_node(self, node_id: str) -> None:
        node = self.runner.node_by_id.get(node_id)
        if node is None:
            return
        if node.get("type") == "control.loop":
            await self.run_loop(node)
            await self.propagate(node, self.context[node_id])
            return
        run_node = node
        if node.get("type") == "control.merge":
            mode = str((node.get("config") or {}).get("mode") or "wait_all")
            if mode in {"wait_all", "collect"}:
                sources = self.incoming_sources.get(node_id, [])
            elif mode in {"first_success", "quorum"}:
                sources = self.successful.get(node_id, [])
            else:
                sources = self.arrivals.get(node_id, [])
            run_node = {**node, "config": {**(node.get("config") or {}), "__merge_source_ids": sources}}
        entry = await self.runner.run_single(run_node, self.context)
        await self.propagate(node, entry)

    async def propagate(self, node: dict[str, Any], entry: dict[str, Any]) -> None:
        node_id = str(node.get("id") or "")
        node_type = str(node.get("type") or "")
        failed = entry.get("status") in {"FAILED", "TIMED_OUT"}
        on_error = str((node.get("config") or {}).get("on_error", "stop"))
        outs = self.runner.outgoing.get(node_id, [])
        if node_type == "control.loop":
            for edge in outs:
                branch = _edge_branch(edge)
                if branch != "body":
                    await self.fire(str(edge["target"]), live=branch not in {"error", "timeout"}, source=node_id)
        elif node_type == "control.try" and not failed:
            branch = "success" if bool((entry.get("output") or {}).get("ok")) else "error"
            for edge in outs:
                await self.fire(str(edge["target"]), live=(_edge_branch(edge) or "success") == branch, source=node_id)
        elif node_type == "condition.if" and not failed:
            branch = "true" if (entry.get("output") or {}).get("result") else "false"
            for edge in outs:
                await self.fire(str(edge["target"]), live=(_edge_branch(edge) or "true") == branch, source=node_id)
        elif node_type == "control.circuit_breaker" and not failed:
            output = entry.get("output") or {}
            branch = "allowed" if str(output.get("operation") or "") != "check" else (
                "allowed" if bool(output.get("allowed")) else "blocked"
            )
            for edge in outs:
                await self.fire(str(edge["target"]), live=(_edge_branch(edge) or "allowed") == branch, source=node_id)
        elif failed and on_error == "branch":
            failure_branch = "timeout" if entry.get("status") == "TIMED_OUT" else "error"
            has_timeout_route = any(_edge_branch(edge) == "timeout" for edge in outs)
            for edge in outs:
                branch = _edge_branch(edge)
                live = branch == failure_branch or (
                    failure_branch == "timeout" and not has_timeout_route and branch == "error"
                )
                await self.fire(str(edge["target"]), live=live, source=node_id)
        else:
            for edge in outs:
                await self.fire(str(edge["target"]), live=_edge_branch(edge) not in {"error", "timeout"}, source=node_id)

    async def run_loop(self, node: dict[str, Any]) -> None:
        node_id = str(node["id"])
        config = node.get("config") or {}
        entry: dict[str, Any] = {"status": "RUNNING", "started_at": _now(), "name": node.get("name") or node_id, "type": "control.loop"}
        self.context[node_id] = entry
        if str(config.get("mode", "count")) == "foreach":
            raw = render_template(str(config.get("items", "")), self.context).strip()
            try:
                parsed = json.loads(raw)
                items: list[Any] = parsed if isinstance(parsed, list) else [parsed]
            except json.JSONDecodeError:
                items = [line for line in raw.splitlines() if line.strip()]
        else:
            items = list(range(max(1, min(int(config.get("count", 1) or 1), MAX_LOOP_ITEMS))))
        items = items[:MAX_LOOP_ITEMS]
        parallel = max(1, min(int(config.get("parallel", 1) or 1), 5))
        body_edges = [edge for edge in self.runner.outgoing.get(node_id, []) if _edge_branch(edge) == "body"]

        async def one_iteration(index: int, item: Any) -> dict[str, Any]:
            iteration = dict(self.context)
            iteration["__vars__"] = dict(self.context.get("__vars__") or {})
            iteration[node_id] = {
                "status": "RUNNING", "name": entry["name"], "type": "control.loop",
                "output": {"index": index, "item": item, "total": len(items)},
            }
            async with asyncio.TaskGroup() as group:
                sub = _DagRun(self.runner, group, iteration)
                for edge in body_edges:
                    await sub.start(str(edge["target"]))
            return {
                "index": index, "item": item,
                "outputs": {
                    key: value.get("output")
                    for key, value in iteration.items()
                    if (key not in self.context or value is not self.context[key])
                    and isinstance(value, dict) and "output" in value
                },
            }

        results: list[dict[str, Any]] = []
        for base in range(0, len(items), parallel):
            batch = list(enumerate(items))[base : base + parallel]
            if parallel <= 1:
                for index, item in batch:
                    results.append(await one_iteration(index, item))
            else:
                results.extend(await asyncio.gather(*(one_iteration(index, item) for index, item in batch)))
        last = results[-1]["outputs"] if results else {}
        entry.update(
            status="SUCCEEDED", finished_at=_now(),
            output={"iterations": len(results), "results": results, "last": last},
        )


def run_flow(definition: dict[str, Any], input_data: dict[str, Any], *, on_event=None) -> dict[str, Any]:
    return asyncio.run(FlowRunner(definition, on_event=on_event).run(input_data))
