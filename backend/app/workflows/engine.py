"""ワークフロー実行エンジンとスケジューラー。"""
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

MAX_STEPS = 100
EXECUTION_TIMEOUT = 3600 * 2

# 実行中タスク（キャンセル用）
_running: dict[int, asyncio.Task] = {}


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
        elif ntype not in NODE_EXECUTORS:
            raise DefinitionError(f"未知のノード種類: {ntype}")
    if nodes and triggers != 1:
        raise DefinitionError("トリガーノードは 1 つ必要です")
    for e in edges:
        if e.get("source") not in ids or e.get("target") not in ids:
            raise DefinitionError("エッジの参照先ノードが存在しません")


async def _execute_graph(nodes: list[dict], edges: list[dict], context: dict[str, Any]) -> None:
    node_by_id = {n["id"]: n for n in nodes}
    trigger = next((n for n in nodes if n.get("type") == "trigger"), None)
    if trigger is None:
        raise DefinitionError("トリガーノードがありません")

    steps = 0
    visited: set[str] = set()

    async def run_node(node_id: str) -> None:
        nonlocal steps
        steps += 1
        if steps > MAX_STEPS:
            raise NodeError(f"ステップ数が上限（{MAX_STEPS}）を超えました")
        if node_id in visited:
            return  # ループ防止
        visited.add(node_id)
        node = node_by_id[node_id]
        ntype = node.get("type", "")
        executor = NODE_EXECUTORS.get(ntype)
        if executor is None:
            raise NodeError(f"未知のノード種類: {ntype}")
        entry: dict[str, Any] = {"status": "RUNNING", "started_at": utcnow().isoformat()}
        context[node_id] = entry
        try:
            timeout = NODE_TIMEOUTS.get(ntype, DEFAULT_NODE_TIMEOUT)
            output = await asyncio.wait_for(
                executor(node.get("config") or {}, context), timeout=timeout
            )
            entry.update(status="SUCCEEDED", output=output, finished_at=utcnow().isoformat())
        except asyncio.TimeoutError:
            entry.update(status="TIMED_OUT", error="タイムアウト", finished_at=utcnow().isoformat())
            raise NodeError(f"ノード {node.get('name') or node_id} がタイムアウトしました")
        except asyncio.CancelledError:
            entry.update(status="CANCELED", finished_at=utcnow().isoformat())
            raise
        except NodeError as e:
            entry.update(status="FAILED", error=str(e), finished_at=utcnow().isoformat())
            raise
        except Exception as e:
            entry.update(status="FAILED", error=f"{type(e).__name__}: {e}", finished_at=utcnow().isoformat())
            raise NodeError(str(e))

        # 次のノードへ（条件ノードは結果に一致するブランチのみ）
        outgoing = [e for e in edges if e.get("source") == node_id]
        if ntype == "condition.if":
            branch = "true" if entry["output"].get("result") else "false"
            outgoing = [
                e for e in outgoing
                if (e.get("branch") or e.get("sourceHandle") or "true") == branch
            ]
        for edge in outgoing:
            await run_node(edge["target"])

    await run_node(trigger["id"])


async def run_workflow(workflow_id: int, trigger_type: str = "manual") -> int:
    """実行レコードを作成しバックグラウンドで実行。実行 ID を返す。"""
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
        context: dict[str, Any] = {}
        status = "SUCCEEDED"
        error = ""
        try:
            await asyncio.wait_for(_execute_graph(nodes, edges, context), timeout=EXECUTION_TIMEOUT)
        except asyncio.TimeoutError:
            status, error = "TIMED_OUT", "実行全体がタイムアウトしました"
        except asyncio.CancelledError:
            status, error = "CANCELED", "キャンセルされました"
        except (NodeError, DefinitionError) as e:
            status, error = "FAILED", str(e)
        except Exception as e:  # 想定外
            logger.exception("workflow %s execution failed", workflow_id)
            status, error = "FAILED", f"内部エラー: {type(e).__name__}"
        finally:
            _running.pop(execution_id, None)
            db2 = SessionLocal()
            try:
                row = db2.get(WorkflowExecution, execution_id)
                if row is not None:
                    row.status = status
                    row.error = error
                    row.finished_at = utcnow()
                    row.context_json = json.dumps(context, ensure_ascii=False, default=str)
                    db2.commit()
            finally:
                db2.close()

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
    while True:
        try:
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
