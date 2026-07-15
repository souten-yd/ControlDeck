"""バックグラウンドジョブ基盤（メモリ + DB 永続化ハイブリッド）。

長時間処理（モデル取得/登録、ワークフロー自動ビルド、チャット生成等）をサーバー側
タスクとして実行し、ブラウザを閉じても継続させる。

- メモリ（_jobs）: 実行中の高速イベントストリーム用（WS 配信・低レイテンシ）。
- DB（Job テーブル）: 状態・進捗・結果・主要イベントのスナップショットを永続化。
  バックエンド再起動後も一覧・詳細・結果を参照でき、実行中だったジョブは
  起動時に interrupted としてマークされる（app.jobs.recovery）。

DB 書き込みは要所（作成・状態変化・終了）に限定し、毎トークンでは書かない
（チャットの大量トークンで SSD/DB を圧迫しないため）。
"""
from __future__ import annotations

import asyncio
import heapq
import itertools
import json
import logging
import time
import uuid
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

logger = logging.getLogger("control_deck.jobs")

MAX_JOBS = 100          # メモリに保持する完了ジョブ数の上限
MAX_EVENTS = 300        # ジョブごとのメモリイベント上限
DB_EVENT_SNAPSHOT = 50  # DB に残す末尾イベント件数


@dataclass
class Job:
    id: str
    kind: str  # model.pull / model.register / workflow.build / chat.completion など
    title: str
    status: str = "queued"  # queued / running / succeeded / failed / canceled / interrupted
    progress: dict = field(default_factory=dict)  # {status, completed, total}
    events: list[dict] = field(default_factory=list)
    result: Any = None
    error: str = ""
    owner_user_id: int | None = None
    idempotency_key: str | None = None
    priority: int = 0
    revision: int = 0
    heartbeat_at: float = field(default_factory=time.time)
    created_at: float = field(default_factory=time.time)
    finished_at: float | None = None
    task: asyncio.Task | None = None
    changed: asyncio.Event = field(default_factory=asyncio.Event, repr=False)

    def log(self, message: str, **extra: Any) -> None:
        self.emit({"t": time.time(), "message": message, **extra})

    def emit(self, payload: dict) -> None:
        payload.setdefault("t", time.time())
        self.events.append(payload)
        if len(self.events) > MAX_EVENTS:
            del self.events[: len(self.events) - MAX_EVENTS]
        _notify_job(self)

    def set_progress(self, status: str, completed: int | None = None, total: int | None = None) -> None:
        self.progress = {"status": status, "completed": completed, "total": total}
        _notify_job(self)

    def to_dict(self, with_events_from: int = 0) -> dict:
        return {
            "id": self.id,
            "kind": self.kind,
            "title": self.title,
            "status": self.status,
            "progress": self.progress,
            "events": self.events[with_events_from:],
            "event_count": len(self.events),
            "result": self.result,
            "error": self.error,
            "created_at": self.created_at,
            "finished_at": self.finished_at,
            "owner_user_id": self.owner_user_id,
            "priority": self.priority,
            "revision": self.revision,
            "heartbeat_at": self.heartbeat_at,
        }


_jobs: OrderedDict[str, Job] = OrderedDict()
_pending: list[tuple[int, int, str, Callable[[Job], Awaitable[Any]]]] = []
_sequence = itertools.count()
_running_count = 0
MAX_CONCURRENT = 4
_stream_revision = 0
_global_changed = asyncio.Event()


def _notify_job(job: Job) -> None:
    global _stream_revision
    job.revision += 1
    job.heartbeat_at = time.time()
    _stream_revision += 1
    job.changed.set()
    _global_changed.set()


# ---- DB 同期（同期関数。呼び出し側が to_thread で包む） ----


def _db_write(job: Job, finished: bool = False) -> None:
    from app.database import SessionLocal
    from app.models import Job as JobRow, JobControl
    from app.models import utcnow

    db = SessionLocal()
    try:
        row = db.get(JobRow, job.id)
        if row is None:
            row = JobRow(id=job.id, kind=job.kind, title=job.title, owner_user_id=job.owner_user_id)
            db.add(row)
        control = db.get(JobControl, job.id)
        if control is None:
            control = JobControl(
                job_id=job.id, owner_user_id=job.owner_user_id, kind=job.kind,
                idempotency_key=job.idempotency_key, priority=job.priority,
            )
            db.add(control)
        row.status = job.status
        row.progress_json = json.dumps(job.progress, ensure_ascii=False, default=str)
        row.events_json = json.dumps(job.events[-DB_EVENT_SNAPSHOT:], ensure_ascii=False, default=str)
        row.error = (job.error or "")[:2000]
        if job.result is not None:
            row.result_json = json.dumps(job.result, ensure_ascii=False, default=str)[:20000]
        if finished:
            row.finished_at = utcnow()
        control.heartbeat_at = utcnow()
        control.revision = job.revision
        control.priority = job.priority
        db.commit()
    except Exception:
        logger.exception("job DB write failed (%s)", job.id)
        db.rollback()
    finally:
        db.close()


def create(
    kind: str, title: str, runner: Callable[[Job], Awaitable[Any]],
    owner_user_id: int | None = None,
    *, idempotency_key: str | None = None, priority: int = 0,
) -> Job:
    """ジョブをpriority queueへ登録する。同一owner/kind/keyの有効結果は再利用する。"""
    if idempotency_key:
        if len(idempotency_key) > 160:
            raise ValueError("idempotency_keyは160文字以内です")
        existing = _find_idempotent(owner_user_id, kind, idempotency_key)
        if existing is not None:
            memory = _jobs.get(existing["id"])
            return memory if memory is not None else _dict_to_job(existing)
        _retire_failed_idempotency(owner_user_id, kind, idempotency_key)
    priority = max(-100, min(100, int(priority)))
    job = Job(
        id=uuid.uuid4().hex[:12], kind=kind, title=title, owner_user_id=owner_user_id,
        idempotency_key=idempotency_key, priority=priority,
    )
    _jobs[job.id] = job
    # 完了済みジョブが溜まりすぎたら古い順にメモリから破棄（DB には残る）
    finished = [j for j in _jobs.values() if j.status not in ("queued", "running")]
    for old in finished[: max(0, len(_jobs) - MAX_JOBS)]:
        _jobs.pop(old.id, None)
    _db_write(job)  # queued作成時に1回
    heapq.heappush(_pending, (-priority, next(_sequence), job.id, runner))
    _notify_job(job)
    _dispatch()
    return job


def _dispatch() -> None:
    global _running_count
    while _running_count < MAX_CONCURRENT and _pending:
        _, _, job_id, runner = heapq.heappop(_pending)
        job = _jobs.get(job_id)
        if job is None or job.status != "queued":
            continue
        job.status = "running"
        _running_count += 1
        _notify_job(job)
        job.task = asyncio.create_task(_run_job(job, runner))


async def _heartbeat(job: Job) -> None:
    while job.status == "running":
        await asyncio.sleep(15)
        if job.status == "running":
            _notify_job(job)
            await asyncio.to_thread(_db_touch_control, job)


async def _run_job(job: Job, runner: Callable[[Job], Awaitable[Any]]) -> None:
    global _running_count
    heartbeat = asyncio.create_task(_heartbeat(job))
    try:
        job.result = await runner(job)
        job.status = "succeeded"
    except asyncio.CancelledError:
        job.status = "canceled"
        job.error = "キャンセルされました"
    except Exception as e:  # ジョブ失敗は記録して終わり（プロセスは守る）
        job.status = "failed"
        job.error = f"{type(e).__name__}: {e}"[:500]
        logger.warning("job %s (%s) failed: %s", job.id, job.kind, job.error)
    finally:
        heartbeat.cancel()
        job.finished_at = time.time()
        _notify_job(job)
        await asyncio.to_thread(_db_write, job, True)
        _running_count = max(0, _running_count - 1)
        _dispatch()


def _db_touch_control(job: Job) -> None:
    from app.database import SessionLocal
    from app.models import JobControl, utcnow

    with SessionLocal() as db:
        control = db.get(JobControl, job.id)
        if control:
            control.heartbeat_at = utcnow()
            control.revision = job.revision
            db.commit()


def _find_idempotent(owner_user_id: int | None, kind: str, key: str) -> dict | None:
    from sqlalchemy import select
    from app.database import SessionLocal
    from app.models import Job as JobRow, JobControl

    with SessionLocal() as db:
        query = (
            select(JobRow).join(JobControl, JobControl.job_id == JobRow.id)
            .where(JobControl.owner_user_id == owner_user_id, JobControl.kind == kind,
                   JobControl.idempotency_key == key,
                   JobRow.status.in_(("queued", "running", "succeeded")))
            .order_by(JobRow.created_at.desc()).limit(1)
        )
        row = db.execute(query).scalar_one_or_none()
        return _row_to_dict(row) if row else None


def _retire_failed_idempotency(owner_user_id: int | None, kind: str, key: str) -> None:
    from sqlalchemy import select
    from app.database import SessionLocal
    from app.models import JobControl

    with SessionLocal() as db:
        rows = db.execute(select(JobControl).where(
            JobControl.owner_user_id == owner_user_id, JobControl.kind == kind,
            JobControl.idempotency_key == key,
        )).scalars().all()
        for control in rows:
            control.idempotency_key = f"{key[:120]}:retired:{control.job_id}"
        if rows:
            db.commit()


def _dict_to_job(data: dict) -> Job:
    return Job(
        id=data["id"], kind=data["kind"], title=data["title"], status=data["status"],
        progress=data.get("progress") or {}, events=data.get("events") or [], result=data.get("result"),
        error=data.get("error") or "", owner_user_id=data.get("owner_user_id"),
        priority=data.get("priority") or 0, revision=data.get("revision") or 0,
        created_at=data.get("created_at") or time.time(), finished_at=data.get("finished_at"),
        heartbeat_at=data.get("heartbeat_at") or time.time(),
    )


def get(job_id: str) -> Job | None:
    return _jobs.get(job_id)


async def get_any(job_id: str) -> dict | None:
    """メモリに無ければ DB から取得（再起動後の履歴参照用）。"""
    job = _jobs.get(job_id)
    if job is not None:
        return job.to_dict()
    return await asyncio.to_thread(_db_get, job_id)


def _db_get(job_id: str) -> dict | None:
    from app.database import SessionLocal
    from app.models import Job as JobRow, JobControl

    db = SessionLocal()
    try:
        row = db.get(JobRow, job_id)
        return _row_to_dict(row, db.get(JobControl, job_id)) if row else None
    finally:
        db.close()


def _row_to_dict(row, control=None) -> dict:
    return {
        "id": row.id, "kind": row.kind, "title": row.title, "status": row.status,
        "progress": json.loads(row.progress_json or "{}"),
        "events": json.loads(row.events_json or "[]"),
        "result": json.loads(row.result_json) if row.result_json else None,
        "error": row.error, "created_at": row.created_at.timestamp() if row.created_at else None,
        "finished_at": row.finished_at.timestamp() if row.finished_at else None,
        "owner_user_id": row.owner_user_id,
        "priority": control.priority if control else 0,
        "revision": control.revision if control else 0,
        "heartbeat_at": control.heartbeat_at.timestamp() if control and control.heartbeat_at else None,
        "persisted": True,
    }


def list_jobs(kind_prefix: str = "", limit: int = 30) -> list[Job]:
    items = [j for j in reversed(_jobs.values()) if j.kind.startswith(kind_prefix)]
    return items[:limit]


async def list_any(kind_prefix: str = "", limit: int = 30, owner_user_id: int | None = None) -> list[dict]:
    """メモリ + DB を統合した一覧（DB を正とし、メモリの実行中で上書き）。"""
    mem = {j.id: j.to_dict() for j in _jobs.values()
           if j.kind.startswith(kind_prefix) and j.owner_user_id in (None, owner_user_id)}
    db_rows = await asyncio.to_thread(_db_list, kind_prefix, limit, owner_user_id)
    merged: dict[str, dict] = {r["id"]: r for r in db_rows}
    merged.update(mem)  # 実行中のメモリ状態を優先
    out = sorted(merged.values(), key=lambda d: d.get("created_at") or 0, reverse=True)
    return out[:limit]


def _db_list(kind_prefix: str, limit: int, owner_user_id: int | None = None) -> list[dict]:
    from sqlalchemy import select

    from app.database import SessionLocal
    from app.models import Job as JobRow, JobControl

    db = SessionLocal()
    try:
        q = select(JobRow).where(
            (JobRow.owner_user_id == owner_user_id) | (JobRow.owner_user_id.is_(None))
        )
        if kind_prefix:
            q = q.where(JobRow.kind.like(f"{kind_prefix}%"))
        q = q.order_by(JobRow.created_at.desc()).limit(limit)
        rows = db.execute(q).scalars().all()
        controls = {c.job_id: c for c in db.execute(
            select(JobControl).where(JobControl.job_id.in_([row.id for row in rows]))
        ).scalars().all()} if rows else {}
        return [_row_to_dict(r, controls.get(r.id)) for r in rows]
    finally:
        db.close()


def cancel(job_id: str) -> bool:
    job = _jobs.get(job_id)
    if job and job.status == "queued":
        job.status = "canceled"
        job.error = "キュー投入後にキャンセルされました"
        job.finished_at = time.time()
        _notify_job(job)
        _db_write(job, True)
        return True
    if job and job.status == "running" and job.task and not job.task.done():
        job.task.cancel()
        return True
    return False


def visible_to(job: Job | dict, owner_user_id: int) -> bool:
    owner = job.owner_user_id if isinstance(job, Job) else job.get("owner_user_id")
    return owner is None or owner == owner_user_id


async def wait_events(job: Job, from_index: int, timeout: float = 25.0) -> int:
    """pollせず通知eventでイベント追加/状態変化を待つ。"""
    if len(job.events) <= from_index and job.status in ("queued", "running"):
        job.changed.clear()
        if len(job.events) <= from_index and job.status in ("queued", "running"):
            try:
                await asyncio.wait_for(job.changed.wait(), timeout=timeout)
            except TimeoutError:
                pass
    return len(job.events)


async def wait_global(from_revision: int, timeout: float = 25.0) -> int:
    if _stream_revision <= from_revision:
        _global_changed.clear()
        if _stream_revision <= from_revision:
            try:
                await asyncio.wait_for(_global_changed.wait(), timeout=timeout)
            except TimeoutError:
                pass
    return _stream_revision


def stream_revision() -> int:
    return _stream_revision


def recover_on_startup() -> int:
    """起動時に、前回queued/runningのまま残ったDBジョブをinterruptedにする。

    メモリは再起動で消えているため、未完了の行は復元不能。件数を返す。
    """
    from app.database import SessionLocal
    from app.models import Job as JobRow
    from app.models import utcnow

    db = SessionLocal()
    try:
        rows = db.query(JobRow).filter(JobRow.status.in_(("queued", "running"))).all()
        for row in rows:
            row.status = "interrupted"
            row.error = row.error or "バックエンド再起動により中断されました"
            row.finished_at = utcnow()
        if rows:
            db.commit()
            logger.info("%d 件の実行中ジョブを interrupted としてマーク", len(rows))
        return len(rows)
    finally:
        db.close()
