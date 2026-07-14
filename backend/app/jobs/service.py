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
    status: str = "running"  # running / succeeded / failed / canceled / interrupted
    progress: dict = field(default_factory=dict)  # {status, completed, total}
    events: list[dict] = field(default_factory=list)
    result: Any = None
    error: str = ""
    owner_user_id: int | None = None
    created_at: float = field(default_factory=time.time)
    finished_at: float | None = None
    task: asyncio.Task | None = None

    def log(self, message: str, **extra: Any) -> None:
        self.events.append({"t": time.time(), "message": message, **extra})
        if len(self.events) > MAX_EVENTS:
            del self.events[: len(self.events) - MAX_EVENTS]

    def set_progress(self, status: str, completed: int | None = None, total: int | None = None) -> None:
        self.progress = {"status": status, "completed": completed, "total": total}

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
        }


_jobs: OrderedDict[str, Job] = OrderedDict()


# ---- DB 同期（同期関数。呼び出し側が to_thread で包む） ----


def _db_write(job: Job, finished: bool = False) -> None:
    from app.database import SessionLocal
    from app.models import Job as JobRow
    from app.models import utcnow

    db = SessionLocal()
    try:
        row = db.get(JobRow, job.id)
        if row is None:
            row = JobRow(id=job.id, kind=job.kind, title=job.title, owner_user_id=job.owner_user_id)
            db.add(row)
        row.status = job.status
        row.progress_json = json.dumps(job.progress, ensure_ascii=False, default=str)
        row.events_json = json.dumps(job.events[-DB_EVENT_SNAPSHOT:], ensure_ascii=False, default=str)
        row.error = (job.error or "")[:2000]
        if job.result is not None:
            row.result_json = json.dumps(job.result, ensure_ascii=False, default=str)[:20000]
        if finished:
            row.finished_at = utcnow()
        db.commit()
    except Exception:
        logger.exception("job DB write failed (%s)", job.id)
        db.rollback()
    finally:
        db.close()


def create(
    kind: str, title: str, runner: Callable[[Job], Awaitable[Any]],
    owner_user_id: int | None = None,
) -> Job:
    """ジョブを登録して即座にバックグラウンド実行を開始する（DB にも記録）。"""
    job = Job(id=uuid.uuid4().hex[:12], kind=kind, title=title, owner_user_id=owner_user_id)
    _jobs[job.id] = job
    # 完了済みジョブが溜まりすぎたら古い順にメモリから破棄（DB には残る）
    finished = [j for j in _jobs.values() if j.status != "running"]
    for old in finished[: max(0, len(_jobs) - MAX_JOBS)]:
        _jobs.pop(old.id, None)
    _db_write(job)  # 作成時に1回（稀なので同期でよい）

    async def _run() -> None:
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
            job.finished_at = time.time()
            await asyncio.to_thread(_db_write, job, True)

    job.task = asyncio.create_task(_run())
    return job


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
    from app.models import Job as JobRow

    db = SessionLocal()
    try:
        row = db.get(JobRow, job_id)
        return _row_to_dict(row) if row else None
    finally:
        db.close()


def _row_to_dict(row) -> dict:
    return {
        "id": row.id, "kind": row.kind, "title": row.title, "status": row.status,
        "progress": json.loads(row.progress_json or "{}"),
        "events": json.loads(row.events_json or "[]"),
        "result": json.loads(row.result_json) if row.result_json else None,
        "error": row.error, "created_at": row.created_at.timestamp() if row.created_at else None,
        "finished_at": row.finished_at.timestamp() if row.finished_at else None,
        "persisted": True,
    }


def list_jobs(kind_prefix: str = "", limit: int = 30) -> list[Job]:
    items = [j for j in reversed(_jobs.values()) if j.kind.startswith(kind_prefix)]
    return items[:limit]


async def list_any(kind_prefix: str = "", limit: int = 30) -> list[dict]:
    """メモリ + DB を統合した一覧（DB を正とし、メモリの実行中で上書き）。"""
    mem = {j.id: j.to_dict() for j in _jobs.values() if j.kind.startswith(kind_prefix)}
    db_rows = await asyncio.to_thread(_db_list, kind_prefix, limit)
    merged: dict[str, dict] = {r["id"]: r for r in db_rows}
    merged.update(mem)  # 実行中のメモリ状態を優先
    out = sorted(merged.values(), key=lambda d: d.get("created_at") or 0, reverse=True)
    return out[:limit]


def _db_list(kind_prefix: str, limit: int) -> list[dict]:
    from sqlalchemy import select

    from app.database import SessionLocal
    from app.models import Job as JobRow

    db = SessionLocal()
    try:
        q = select(JobRow).order_by(JobRow.created_at.desc()).limit(limit)
        if kind_prefix:
            q = select(JobRow).where(JobRow.kind.like(f"{kind_prefix}%")).order_by(JobRow.created_at.desc()).limit(limit)
        return [_row_to_dict(r) for r in db.execute(q).scalars().all()]
    finally:
        db.close()


def cancel(job_id: str) -> bool:
    job = _jobs.get(job_id)
    if job and job.status == "running" and job.task and not job.task.done():
        job.task.cancel()
        return True
    return False


async def wait_events(job: Job, from_index: int, timeout: float = 25.0) -> int:
    """イベントが増えるかジョブ終了まで待つ（WS ストリーム用）。新しい件数を返す。"""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if len(job.events) > from_index or job.status != "running":
            break
        await asyncio.sleep(0.4)
    return len(job.events)


def recover_on_startup() -> int:
    """起動時に、前回実行中(running)のまま残った DB ジョブを interrupted にする。

    メモリは再起動で消えているため、running のままの行は復元不能。件数を返す。
    """
    from app.database import SessionLocal
    from app.models import Job as JobRow
    from app.models import utcnow

    db = SessionLocal()
    try:
        rows = db.query(JobRow).filter(JobRow.status == "running").all()
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
