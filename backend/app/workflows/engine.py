"""ワークフロー実行エンジンとスケジューラー（v2: 並列 DAG 実行）。

v2 の実行モデル:
- ノードは「最初の生きた入力」で発火（従来互換）。config.join=="all" で全入力待ち合流。
- 分岐で選ばれなかった経路には dead 信号を伝播し、合流ノードの待ちを解決する。
- 独立した枝は並列実行（同時実行ノード数は MAX_PARALLEL_NODES で制限）。
- ノード共通設定: retry_count / retry_wait / on_error(stop|continue|branch) /
  require_approval(実行前承認) / join。エラー分岐は branch=="error" のエッジへ。
- 実行中コンテキストは _live からライブ参照でき、定期的に DB へフラッシュされる。
"""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select

from app.database import SessionLocal
from app.models import Workflow, WorkflowExecution, utcnow
from app.workflows.nodes import (
    DEFAULT_NODE_TIMEOUT,
    NODE_EXECUTORS,
    NODE_TIMEOUTS,
    NodeError,
)

logger = logging.getLogger("control_deck.workflows")

MAX_STEPS = 300
MAX_PARALLEL_NODES = 4
EXECUTION_TIMEOUT = 3600 * 2
APPROVAL_TIMEOUT = 86400  # 承認待ちの上限（秒）
MAX_SUBFLOW_DEPTH = 3

# セマフォを持たずに実行するノード（待機・サブフロー等。枠を長時間占有させない）
_UNMETERED = {"util.wait", "flow.call", "trigger"}

# 実行中タスク（キャンセル用）と、ライブ参照用のコンテキスト
_running: dict[int, asyncio.Task] = {}
_live: dict[int, dict] = {}
# 承認待ち: (execution_id, node_id) -> Future[bool]
_approvals: dict[tuple[int, str], asyncio.Future] = {}


class DefinitionError(ValueError):
    pass


def parse_definition(definition_json: str) -> tuple[list[dict], list[dict]]:
    try:
        d = json.loads(definition_json or "{}")
    except json.JSONDecodeError as e:
        raise DefinitionError(f"定義が不正な JSON です: {e}")
    nodes = d.get("nodes", [])
    edges = d.get("edges", [])
    if not isinstance(nodes, list) or not isinstance(edges, list):
        raise DefinitionError("nodes / edges は配列である必要があります")
    return nodes, edges


def validate_definition(definition_json: str) -> None:
    nodes, edges = parse_definition(definition_json)
    ids = set()
    triggers = 0
    for n in nodes:
        nid = n.get("id")
        ntype = n.get("type")
        if not nid or nid in ids:
            raise DefinitionError(f"ノード ID が重複または欠落しています: {nid}")
        ids.add(nid)
        if ntype == "trigger":
            triggers += 1
        elif ntype == "control.loop":
            pass  # エンジンが直接処理する制御ノード
        elif ntype not in NODE_EXECUTORS:
            raise DefinitionError(f"未知のノード種類: {ntype}")
    if nodes and triggers != 1:
        raise DefinitionError("トリガーノードは 1 つ必要です")
    for e in edges:
        if e.get("source") not in ids or e.get("target") not in ids:
            raise DefinitionError("エッジの参照先ノードが存在しません")


def _edge_branch(e: dict) -> str | None:
    return e.get("branch") or e.get("sourceHandle") or None


# ---- ライブ状況・承認 ----


def live_context(execution_id: int) -> dict | None:
    """実行中コンテキストのライブ参照（終了後は None → DB を見る）。"""
    return _live.get(execution_id)


def pending_approvals(execution_id: int) -> list[str]:
    return [nid for (eid, nid) in _approvals if eid == execution_id]


def resolve_approval(execution_id: int, node_id: str, approve: bool) -> bool:
    fut = _approvals.get((execution_id, node_id))
    if fut is None or fut.done():
        return False
    fut.set_result(approve)
    return True


def _set_exec_status(execution_id: int, status: str) -> None:
    db = SessionLocal()
    try:
        row = db.get(WorkflowExecution, execution_id)
        if row is not None and row.status in ("RUNNING", "WAITING"):
            row.status = status
            db.commit()
    finally:
        db.close()


def _load_secrets() -> dict[str, str]:
    """{{secrets.名前}} 用。暗号化ストアから全シークレットを復号して返す。"""
    from app.models import WorkflowSecret
    from app.security.crypto import decrypt_text

    db = SessionLocal()
    try:
        out: dict[str, str] = {}
        for s in db.query(WorkflowSecret).all():
            try:
                out[s.name] = decrypt_text(s.value_encrypted)
            except Exception:
                continue
        return out
    finally:
        db.close()


# ---- v2 DAG 実行 ----


async def _execute_graph(
    nodes: list[dict], edges: list[dict], context: dict[str, Any], execution_id: int | None = None
) -> None:
    node_by_id = {n["id"]: n for n in nodes}
    trigger = next((n for n in nodes if n.get("type") == "trigger"), None)
    if trigger is None:
        raise DefinitionError("トリガーノードがありません")

    steps = {"n": 0}
    sem = asyncio.Semaphore(MAX_PARALLEL_NODES)
    outgoing: dict[str, list[dict]] = {}
    for e in edges:
        outgoing.setdefault(e["source"], []).append(e)

    async def run_single(node: dict) -> dict:
        """1 ノードの実行（承認ゲート → リトライ付き実行 → 記録）。"""
        nid, ntype = node["id"], node.get("type", "")
        config = node.get("config") or {}
        steps["n"] += 1
        if steps["n"] > MAX_STEPS:
            raise NodeError(f"ステップ数が上限（{MAX_STEPS}）を超えました")
        executor = NODE_EXECUTORS.get(ntype)
        if executor is None:
            raise NodeError(f"未知のノード種類: {ntype}")
        entry: dict[str, Any] = {"status": "PENDING", "name": node.get("name") or nid, "type": ntype}
        context[nid] = entry

        # 実行前承認ゲート（任意ノードに設定可能）
        if config.get("require_approval") and ntype != "trigger" and execution_id is not None:
            entry.update(status="WAITING_APPROVAL", waiting_since=utcnow().isoformat())
            await asyncio.to_thread(_set_exec_status, execution_id, "WAITING")
            fut: asyncio.Future = asyncio.get_event_loop().create_future()
            _approvals[(execution_id, nid)] = fut
            try:
                approved = await asyncio.wait_for(fut, timeout=APPROVAL_TIMEOUT)
            except asyncio.TimeoutError:
                entry.update(status="FAILED", error="承認待ちがタイムアウトしました", finished_at=utcnow().isoformat())
                raise NodeError(f"ノード {entry['name']} の承認待ちがタイムアウトしました")
            finally:
                _approvals.pop((execution_id, nid), None)
                await asyncio.to_thread(_set_exec_status, execution_id, "RUNNING")
            if not approved:
                entry.update(status="FAILED", error="実行が却下されました", finished_at=utcnow().isoformat())
                if str(config.get("on_error", "stop")) == "stop":
                    raise NodeError(f"ノード {entry['name']} が却下されました")
                return entry

        retries = max(0, min(int(config.get("retry_count", 0) or 0), 5))
        retry_wait = max(0.0, min(float(config.get("retry_wait", 5) or 5), 300.0))
        timeout = NODE_TIMEOUTS.get(ntype, DEFAULT_NODE_TIMEOUT)
        attempt = 0
        entry.update(status="RUNNING", started_at=utcnow().isoformat())
        while True:
            attempt += 1
            try:
                if ntype in _UNMETERED:
                    output = await asyncio.wait_for(executor(config, context), timeout=timeout)
                else:
                    async with sem:
                        output = await asyncio.wait_for(executor(config, context), timeout=timeout)
                entry.update(status="SUCCEEDED", output=output, finished_at=utcnow().isoformat(), attempts=attempt)
                var_name = str(config.get("output_var") or "").strip()
                if var_name:
                    context.setdefault("__vars__", {})[var_name] = output
                return entry
            except asyncio.CancelledError:
                entry.update(status="CANCELED", finished_at=utcnow().isoformat())
                raise
            except asyncio.TimeoutError:
                err, final_status = "タイムアウト", "TIMED_OUT"
            except NodeError as e:
                err, final_status = str(e), "FAILED"
            except Exception as e:  # 想定外もリトライ対象にする
                err, final_status = f"{type(e).__name__}: {e}", "FAILED"
            if attempt <= retries:
                entry.update(status="RETRYING", error=err, attempts=attempt)
                await asyncio.sleep(retry_wait)
                entry["status"] = "RUNNING"
                continue
            entry.update(status=final_status, error=err, finished_at=utcnow().isoformat(), attempts=attempt)
            if str(config.get("on_error", "stop")) == "stop":
                raise NodeError(f"ノード {entry['name']} が失敗しました: {err}")
            return entry

    class DagRun:
        """発火カウント方式の DAG 実行状態（メイン/ループ反復ごとに 1 つ）。"""

        def __init__(self, tg: asyncio.TaskGroup):
            self.tg = tg
            self.lock = asyncio.Lock()
            self.received: dict[str, int] = {}
            self.live_received: dict[str, int] = {}
            self.ran: set[str] = set()
            self.incoming = {nid: 0 for nid in node_by_id}
            for e in edges:
                self.incoming[e["target"]] = self.incoming.get(e["target"], 0) + 1

        async def fire(self, target: str, live: bool) -> None:
            node = node_by_id.get(target)
            if node is None:
                return
            join_all = str((node.get("config") or {}).get("join", "")) == "all"
            async with self.lock:
                self.received[target] = self.received.get(target, 0) + 1
                if live:
                    self.live_received[target] = self.live_received.get(target, 0) + 1
                if target in self.ran:
                    return
                resolved = self.received[target] >= self.incoming.get(target, 0)
                lives = self.live_received.get(target, 0)
                if join_all:
                    if not resolved:
                        return  # 全入力が揃うまで待つ
                    run = lives > 0
                else:
                    run = live  # 最初の生きた入力で発火（従来互換）
                    if not run and not (resolved and lives == 0):
                        return
                if run:
                    self.ran.add(target)
            if run:
                self.tg.create_task(self.exec_node(target))
            else:
                # 全入力が dead → このノードは実行されない。下流へ dead を伝播
                context.setdefault(target, {"status": "SKIPPED"})
                for e in outgoing.get(target, []):
                    await self.fire(e["target"], live=False)

        async def start(self, node_id: str) -> None:
            async with self.lock:
                if node_id in self.ran:
                    return
                self.ran.add(node_id)
            self.tg.create_task(self.exec_node(node_id))

        async def exec_node(self, nid: str) -> None:
            node = node_by_id.get(nid)
            if node is None:
                return
            if node.get("type") == "control.loop":
                await run_loop(node)
                for e in outgoing.get(nid, []):
                    br = _edge_branch(e)
                    if br == "body":
                        continue
                    await self.fire(e["target"], live=br != "error")
                return
            entry = await run_single(node)
            failed = entry["status"] in ("FAILED", "TIMED_OUT")
            on_error = str((node.get("config") or {}).get("on_error", "stop"))
            outs = outgoing.get(nid, [])
            if node.get("type") == "condition.if" and not failed:
                branch = "true" if (entry.get("output") or {}).get("result") else "false"
                for e in outs:
                    await self.fire(e["target"], live=(_edge_branch(e) or "true") == branch)
            elif failed and on_error == "branch":
                for e in outs:
                    await self.fire(e["target"], live=_edge_branch(e) == "error")
            else:  # 成功、または continue で失敗を無視して先へ
                for e in outs:
                    await self.fire(e["target"], live=_edge_branch(e) != "error")

    async def run_loop(node: dict) -> None:
        from app.workflows.nodes import render_template

        node_id = node["id"]
        config = node.get("config") or {}
        mode = config.get("mode", "count")
        entry: dict[str, Any] = {"status": "RUNNING", "started_at": utcnow().isoformat(),
                                 "name": node.get("name") or node_id, "type": "control.loop"}
        context[node_id] = entry

        items: list[Any]
        if mode == "foreach":
            raw = render_template(str(config.get("items", "")), context).strip()
            try:
                parsed = json.loads(raw)
                items = parsed if isinstance(parsed, list) else [parsed]
            except json.JSONDecodeError:
                items = [line for line in raw.splitlines() if line.strip()]
        else:
            count = max(1, min(int(config.get("count", 1) or 1), 100))
            items = list(range(count))
        items = items[:100]
        parallel = max(1, min(int(config.get("parallel", 1) or 1), 5))
        body_edges = [e for e in outgoing.get(node_id, []) if _edge_branch(e) == "body"]

        async def one_iteration(index: int, item: Any) -> None:
            entry["output"] = {"index": index, "item": item, "total": len(items)}
            async with asyncio.TaskGroup() as tg2:
                sub = DagRun(tg2)
                for e in body_edges:
                    await sub.start(e["target"])

        if parallel <= 1:
            for index, item in enumerate(items):
                await one_iteration(index, item)
        else:
            # 並列 foreach: item/index はイテレーションで共有 context を上書きするため、
            # 並列時は {{ID.item}} の参照が不定になる。バッチごとに逐次上書きして実行する
            for base in range(0, len(items), parallel):
                batch = list(enumerate(items))[base : base + parallel]
                await asyncio.gather(*(one_iteration(i, it) for i, it in batch))
        entry.update(
            status="SUCCEEDED",
            output={"index": len(items) - 1, "item": items[-1] if items else None, "total": len(items), "done": True},
            finished_at=utcnow().isoformat(),
        )

    async with asyncio.TaskGroup() as tg:
        dag = DagRun(tg)
        await dag.start(trigger["id"])


# ---- 実行管理 ----


async def run_workflow(
    workflow_id: int, trigger_type: str = "manual", input_data: dict | None = None, depth: int = 0
) -> int:
    """実行レコードを作成しバックグラウンドで実行。実行 ID を返す。

    input_data: チャットフロー等の入力（trigger ノードの出力へ展開される）。
    depth: サブフロー呼び出しの深さ（flow.call の再帰暴走防止）。
    """
    if depth > MAX_SUBFLOW_DEPTH:
        raise DefinitionError(f"サブフローの深さが上限（{MAX_SUBFLOW_DEPTH}）を超えました")
    db = SessionLocal()
    try:
        wf = db.get(Workflow, workflow_id)
        if wf is None:
            raise DefinitionError("ワークフローが見つかりません")
        nodes, edges = parse_definition(wf.definition_json)
        execution = WorkflowExecution(
            workflow_id=workflow_id, status="RUNNING", trigger_type=trigger_type
        )
        db.add(execution)
        db.commit()
        execution_id = execution.id
    finally:
        db.close()

    async def runner() -> None:
        context: dict[str, Any] = {
            "__input__": input_data or {},
            "__depth__": depth,
            "__secrets__": await asyncio.to_thread(_load_secrets),
        }
        _live[execution_id] = context
        status = "SUCCEEDED"
        error = ""

        def flush(final_status: str | None = None) -> None:
            db2 = SessionLocal()
            try:
                row = db2.get(WorkflowExecution, execution_id)
                if row is None:
                    return
                saved = {k: v for k, v in context.items() if k not in ("__secrets__",)}
                row.context_json = json.dumps(saved, ensure_ascii=False, default=str)
                if final_status is not None:
                    row.status = final_status
                    row.error = error
                    row.finished_at = utcnow()
                db2.commit()
            finally:
                db2.close()

        async def flusher() -> None:
            while True:
                await asyncio.sleep(3)
                await asyncio.to_thread(flush)

        def _flatten(exc: BaseException) -> list[BaseException]:
            if isinstance(exc, BaseExceptionGroup):
                out: list[BaseException] = []
                for sub in exc.exceptions:
                    out.extend(_flatten(sub))
                return out
            return [exc]

        flush_task = asyncio.create_task(flusher())
        try:
            await asyncio.wait_for(_execute_graph(nodes, edges, context, execution_id), timeout=EXECUTION_TIMEOUT)
        except asyncio.TimeoutError:
            status, error = "TIMED_OUT", "実行全体がタイムアウトしました"
        except asyncio.CancelledError:
            status, error = "CANCELED", "キャンセルされました"
        except (NodeError, DefinitionError) as e:
            status, error = "FAILED", str(e)[:500]
        except BaseExceptionGroup as eg:  # TaskGroup からの複合例外
            causes = _flatten(eg)
            if any(isinstance(c, asyncio.CancelledError) for c in causes):
                status, error = "CANCELED", "キャンセルされました"
            elif all(isinstance(c, (NodeError, DefinitionError)) for c in causes):
                status, error = "FAILED", "; ".join(str(c) for c in causes)[:500]
            else:
                logger.exception("workflow %s execution failed", workflow_id)
                status, error = "FAILED", "; ".join(f"{type(c).__name__}: {c}" for c in causes)[:500]
        except Exception:  # 想定外
            logger.exception("workflow %s execution failed", workflow_id)
            status, error = "FAILED", "内部エラー"
        finally:
            flush_task.cancel()
            _running.pop(execution_id, None)
            _live.pop(execution_id, None)
            # 残った承認待ちを掃除
            for key in [k for k in _approvals if k[0] == execution_id]:
                fut = _approvals.pop(key)
                if not fut.done():
                    fut.cancel()
            await asyncio.to_thread(flush, status)

    task = asyncio.create_task(runner())
    _running[execution_id] = task
    return execution_id


def cancel_execution(execution_id: int) -> bool:
    task = _running.get(execution_id)
    if task and not task.done():
        task.cancel()
        return True
    return False


# ---- スケジューラー ----


def _next_run_after(trigger_config: dict, last: datetime | None, now: datetime) -> bool:
    """トリガー設定に基づき、いま実行すべきか判定する。"""
    mode = trigger_config.get("mode", "manual")
    if mode == "interval":
        minutes = max(1, int(trigger_config.get("interval_minutes", 60)))
        if last is None:
            return True
        return now - last >= timedelta(minutes=minutes)
    if mode == "daily":
        hhmm = str(trigger_config.get("time", "08:00"))
        try:
            hour, minute = (int(x) for x in hhmm.split(":"))
        except ValueError:
            return False
        today_at = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if now < today_at:
            return False
        return last is None or last < today_at
    if mode == "cron":
        expr = str(trigger_config.get("cron", ""))
        try:
            from croniter import croniter

            base = last or (now - timedelta(days=1))
            next_time = croniter(expr, base).get_next(datetime)
            return next_time <= now
        except Exception:
            return False
    return False


async def scheduler_loop() -> None:
    """30 秒ごとに有効なワークフローのスケジュールトリガーを評価する。"""
    from app.maintenance.watchdog import beat

    while True:
        try:
            beat("scheduler")
            await asyncio.sleep(30)
            now = datetime.now(timezone.utc)

            def find_due() -> list[tuple[int, dict]]:
                db = SessionLocal()
                due: list[tuple[int, dict]] = []
                try:
                    rows = db.execute(select(Workflow).where(Workflow.enabled.is_(True))).scalars().all()
                    for wf in rows:
                        try:
                            nodes, _ = parse_definition(wf.definition_json)
                        except DefinitionError:
                            continue
                        trigger = next((n for n in nodes if n.get("type") == "trigger"), None)
                        if trigger is None:
                            continue
                        config = trigger.get("config") or {}
                        if config.get("mode") in ("interval", "daily", "cron"):
                            last_row = db.execute(
                                select(WorkflowExecution.started_at)
                                .where(WorkflowExecution.workflow_id == wf.id)
                                .order_by(WorkflowExecution.started_at.desc())
                                .limit(1)
                            ).scalar_one_or_none()
                            if last_row is not None and last_row.tzinfo is None:
                                last_row = last_row.replace(tzinfo=timezone.utc)
                            if _next_run_after(config, last_row, now):
                                due.append((wf.id, config))
                    return due
                finally:
                    db.close()

            for wf_id, _config in await asyncio.to_thread(find_due):
                logger.info("scheduled workflow %s triggered", wf_id)
                await run_workflow(wf_id, trigger_type="schedule")
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("scheduler loop error")


# ---- イベントトリガー（アラート連動） ----


async def fire_event_triggers(event_source: str, payload: dict) -> list[int]:
    """イベント発生時に、該当するイベントトリガーのワークフローを起動する。

    trigger.config: {mode: "event", event_source: "alert", rule_filter: "部分一致(任意)"}
    """
    def find_targets() -> list[int]:
        db = SessionLocal()
        try:
            targets: list[int] = []
            rows = db.execute(select(Workflow).where(Workflow.enabled.is_(True))).scalars().all()
            for wf in rows:
                try:
                    nodes, _ = parse_definition(wf.definition_json)
                except DefinitionError:
                    continue
                trigger = next((n for n in nodes if n.get("type") == "trigger"), None)
                if trigger is None:
                    continue
                config = trigger.get("config") or {}
                if config.get("mode") != "event" or config.get("event_source", "alert") != event_source:
                    continue
                rule_filter = str(config.get("rule_filter", "") or "").strip()
                if rule_filter and rule_filter not in str(payload.get("rule", "")):
                    continue
                targets.append(wf.id)
            return targets
        finally:
            db.close()

    execution_ids = []
    for wf_id in await asyncio.to_thread(find_targets):
        try:
            execution_ids.append(await run_workflow(wf_id, trigger_type="event", input_data=payload))
            logger.info("event trigger (%s) fired workflow %s", event_source, wf_id)
        except DefinitionError:
            continue
    return execution_ids
