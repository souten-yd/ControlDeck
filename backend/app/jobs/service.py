"""バックグラウンドジョブ基盤。

長時間処理（モデル取得/登録、ワークフロー自動ビルド等）をサーバー側タスクとして
実行し、ブラウザを閉じても継続させる。UI は /jobs API でポーリング・再接続できる。
プロセス内レジストリ（サーバー再起動で消える。履歴は監査ログに残る）。
"""
from __future__ import annotations

import asyncio
import logging
import time
import uuid
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

logger = logging.getLogger("control_deck.jobs")

MAX_JOBS = 100  # 保持する完了ジョブ数の上限
MAX_EVENTS = 300  # ジョブごとのイベントログ上限


@dataclass
class Job:
    id: str
    kind: str  # model.pull / model.register / workflow.build など
    title: str
    status: str = "running"  # running / succeeded / failed / canceled
    progress: dict = field(default_factory=dict)  # {status, completed, total}
    events: list[dict] = field(default_factory=list)
    result: Any = None
    error: str = ""
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


def create(kind: str, title: str, runner: Callable[[Job], Awaitable[Any]]) -> Job:
    """ジョブを登録して即座にバックグラウンド実行を開始する。"""
    job = Job(id=uuid.uuid4().hex[:12], kind=kind, title=title)
    _jobs[job.id] = job
    # 完了済みジョブが溜まりすぎたら古い順に破棄
    finished = [j for j in _jobs.values() if j.status != "running"]
    for old in finished[: max(0, len(_jobs) - MAX_JOBS)]:
        _jobs.pop(old.id, None)

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

    job.task = asyncio.create_task(_run())
    return job


def get(job_id: str) -> Job | None:
    return _jobs.get(job_id)


def list_jobs(kind_prefix: str = "", limit: int = 30) -> list[Job]:
    items = [j for j in reversed(_jobs.values()) if j.kind.startswith(kind_prefix)]
    return items[:limit]


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
