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
import os
import re
import time
import uuid
from collections import OrderedDict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable

from app.resources.broker import BrokerError, ResourceBroker, broker as resource_broker
from app.resources.leases import LeaseError
from app.resources.schema import RequestState, RequestStatus, ResourceRequest

logger = logging.getLogger("control_deck.jobs")

MAX_JOBS = 100          # メモリに保持する完了ジョブ数の上限
MAX_EVENTS = 300        # ジョブごとのメモリイベント上限
DB_EVENT_SNAPSHOT = 50  # DB に残す末尾イベント件数
RESOURCE_PHASES = {
    "waiting_resource", "starting", "generating", "postprocess", "validate", "package"
}
# 再起動で中断された Job を自動で作り直す回数と期限。何度も落ちる種類で
# 再起動のたびに取得をやり直し続けないための歯止め（m3-spec.md）。
RESUME_MAX_ATTEMPTS = 3
RESUME_MAX_AGE_SECONDS = 7 * 24 * 60 * 60
# 利用者が「停止」したまま放置された記録を消すまでの期限。自動再開はしないが、記録は
# 「再開」のために残す。無期限に残すと /jobs の走査（resume_record_ids）が増え続ける。
PAUSED_RESUME_MAX_AGE_SECONDS = 30 * 24 * 60 * 60
_RESUME_NAME_RE = re.compile(r"[A-Za-z0-9_-]{1,64}")
# 再開の処理中の Job（同時に押されても Job を二重に作らない）。event loop は 1 本なので、
# await をまたがない「見て入れる」は原子。錠をモジュールに置くと試験ごとの loop に縛られる。
_resuming: set[str] = set()
TERMINAL_STATUSES = ("succeeded", "failed", "canceled")


@dataclass
class Job:
    id: str
    kind: str  # model.pull / model.register / workflow.build / chat.completion など
    title: str
    status: str = "queued"  # queued / running / succeeded / failed / canceled / interrupted
    phase: str | None = None
    wait_reason: str | None = None
    progress: dict = field(default_factory=dict)  # {status, completed, total}
    events: list[dict] = field(default_factory=list)
    # events は bounded journal。配列 index ではなく単調増加 cursor で購読する。
    # event_offset は現在保持している先頭イベントの直前の sequence。
    event_offset: int = 0
    event_sequence: int = 0
    result: Any = None
    error: str = ""
    owner_user_id: int | None = None
    idempotency_key: str | None = None
    # 再起動後に続きから作り直せる Job か（resume= 付きで作られた Job）。
    resumable: bool = False
    # 利用者の「停止」の要求（stop() が立てる。取り消しとは扱いを分ける）。
    pause_requested: bool = False
    priority: int = 0
    revision: int = 0
    heartbeat_at: float = field(default_factory=time.time)
    created_at: float = field(default_factory=time.time)
    finished_at: float | None = None
    task: asyncio.Task | None = None
    resource_task: asyncio.Task | None = field(default=None, repr=False)
    resource_renew_task: asyncio.Task | None = field(default=None, repr=False)
    resource_request_id: str | None = None
    resource_lease_id: str | None = None
    resource_broker: ResourceBroker | None = field(default=None, repr=False)
    resource_requirement: ResourceRequest | None = field(default=None, repr=False)
    progress_flush_task: asyncio.Task | None = field(default=None, repr=False)
    last_progress_notify_at: float = field(default=0.0, repr=False)
    last_progress_db_at: float = field(default=0.0, repr=False)
    external_addon_id: str | None = field(default=None, repr=False)
    last_external_update_at: float = field(default=0.0, repr=False)
    changed: asyncio.Event = field(default_factory=asyncio.Event, repr=False)

    def log(self, message: str, **extra: Any) -> None:
        self.emit({"t": time.time(), "message": message, **extra})

    def emit(self, payload: dict) -> None:
        payload.setdefault("t", time.time())
        self.event_sequence += 1
        payload["_seq"] = self.event_sequence
        self.events.append(payload)
        if len(self.events) > MAX_EVENTS:
            removed = len(self.events) - MAX_EVENTS
            del self.events[:removed]
            self.event_offset += removed
        _notify_job(self)

    def set_progress(self, status: str, completed: int | None = None, total: int | None = None) -> None:
        if self.resource_broker is None:
            self.progress = {"status": status, "completed": completed, "total": total}
            _notify_job(self)
            return
        phase_changed = status in RESOURCE_PHASES and status != self.phase
        if phase_changed:
            self.phase = status
        if self.phase not in RESOURCE_PHASES:
            raise ValueError("resource-aware job progressにはphaseが必要です")
        previous = self.progress.get("completed")
        if completed is not None and isinstance(previous, int) and completed < previous:
            raise ValueError("resource-aware job progressは単調増加である必要があります")
        self.progress = {
            "status": status,
            "completed": completed,
            "total": total,
            "phase": self.phase,
        }
        now = time.monotonic()
        if phase_changed or now - self.last_progress_notify_at >= 0.5:
            self.last_progress_notify_at = now
            _notify_job(self)
        if phase_changed:
            self.last_progress_db_at = now
            asyncio.create_task(asyncio.to_thread(_db_write, self))
        if self.progress_flush_task is None or self.progress_flush_task.done():
            delay = max(0.0, 0.5 - (now - self.last_progress_notify_at))
            self.progress_flush_task = asyncio.create_task(
                _flush_resource_progress(self, delay)
            )

    def to_dict(self, with_events_from: int = 0) -> dict:
        retained, next_cursor, truncated = self.events_since(with_events_from)
        return {
            "id": self.id,
            "kind": self.kind,
            "title": self.title,
            "status": self.status,
            "phase": self.phase,
            "wait_reason": self.wait_reason,
            "progress": self.progress,
            "events": retained,
            "event_count": self.event_sequence,
            "event_cursor": next_cursor,
            "events_truncated": truncated,
            "result": self.result,
            "error": self.error,
            "created_at": self.created_at,
            "finished_at": self.finished_at,
            "owner_user_id": self.owner_user_id,
            "priority": self.priority,
            "revision": self.revision,
            "heartbeat_at": self.heartbeat_at,
        }

    def events_since(self, cursor: int) -> tuple[list[dict], int, bool]:
        """絶対cursorより後の保持イベントを返す。遅延時はtruncatedを明示する。"""
        cursor = max(0, int(cursor))
        truncated = cursor < self.event_offset
        start = max(cursor, self.event_offset) - self.event_offset
        return self.events[start:], self.event_sequence, truncated


_jobs: OrderedDict[str, Job] = OrderedDict()
_pending: list[tuple[int, int, str, Callable[[Job], Awaitable[Any]]]] = []
_sequence = itertools.count()
_running_count = 0
MAX_CONCURRENT = 4
MAX_EXTERNAL_ACTIVE_PER_OWNER = 8
_stream_revision = 0
_global_changed = asyncio.Event()
_shutdown_started = False


def begin_shutdown() -> None:
    """停止の合図を受けたことを記録する（lifespan の後始末の先頭で呼ぶ）。

    asyncio の loop を閉じるときに残りの task は cancel される。その CancelledError は
    利用者の取り消しと同じ形なので、区別しないと再起動のたびに「キャンセル」と記録され、
    自動再開の対象が消える。停止中は再開できる Job を終わらせずに残す。
    """
    global _shutdown_started
    _shutdown_started = True


def begin_startup() -> None:
    """起動のたびに停止の印を戻す（同じプロセスで lifespan を繰り返す試験のため）。"""
    global _shutdown_started
    _shutdown_started = False


def is_shutting_down() -> bool:
    """サーバーの停止中か（runner が「途中を残す」か「取り消して消す」か決めるために見る）。"""
    return _shutdown_started


def _notify_job(job: Job) -> None:
    global _stream_revision
    job.revision += 1
    job.heartbeat_at = time.time()
    _stream_revision += 1
    job.changed.set()
    _global_changed.set()


async def _flush_resource_progress(job: Job, delay: float) -> None:
    if delay:
        await asyncio.sleep(delay)
    if job.status not in ("queued", "running"):
        return
    now = time.monotonic()
    if now - job.last_progress_notify_at >= 0.49:
        job.last_progress_notify_at = now
        _notify_job(job)
    if now - job.last_progress_db_at >= 5:
        job.last_progress_db_at = now
        await asyncio.to_thread(_db_write, job)


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
        row.phase = job.phase
        row.wait_reason = job.wait_reason
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
        # 終わった Job の再開用の記録は残さない（利用者が止めたもの・失敗したものも
        # 同じ失敗を繰り返さないため再開しない）。DB の更新が済んでから消す。
        if finished and job.resumable and job.status in TERMINAL_STATUSES:
            _remove_resume_file(job.id)
    except Exception:
        logger.exception("job DB write failed (%s)", job.id)
        db.rollback()
    finally:
        db.close()


# ---- 再開できる Job の記録（m3-spec.md） ----
#
# 取得などの長い Job は、サーバーの再起動でメモリの実行状態が消える。Job の種類ごとに
# 「保存した引数から runner を作り直す」関数を登録しておき、中断されたものだけを起動後に
# 裏で作り直す。ランタイム（Strata・Maya・HF）ごとに仕組みを作らない。

ResumeFactory = Callable[[dict[str, Any]], Callable[[Job], Awaitable[Any]]]


@dataclass(frozen=True)
class _ResumableKind:
    factory: ResumeFactory
    title: Callable[[dict[str, Any]], str]


_resumable_kinds: dict[str, _ResumableKind] = {}


def register_resumable(
    kind: str,
    factory: ResumeFactory,
    *,
    title: Callable[[dict[str, Any]], str],
) -> None:
    """再起動後に続きから作り直せる Job の種類を登録する（モジュールの読み込み時に呼ぶ）。

    factory は保存した引数から runner を作る。title は引数から画面に出す題を作る。
    """
    _resumable_kinds[kind] = _ResumableKind(factory=factory, title=title)


def _resume_dir() -> Path:
    from app.config import data_dir

    root = data_dir() / "jobs" / "resumable"
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    return root


def _remove_resume_file(job_id: str) -> None:
    try:
        (_resume_dir() / f"{job_id}.json").unlink(missing_ok=True)
    except OSError:
        logger.warning("再開用の記録を消せませんでした (%s)", job_id)


def _write_resume_file(job: Job, params: dict, *, attempts: int, created_at: float | None = None) -> None:
    """再開に要る引数だけを 0600 の JSON に書く（秘密値は呼び出し側が入れない）。

    一時ファイルへ書いてから rename する（途中で落ちても半端な JSON を残さない）。
    """
    doc = {
        "kind": job.kind,
        "params": params,
        "owner_user_id": job.owner_user_id,
        "title": job.title,
        # 最初に作った時刻を引き継ぐ（再開のたびに 7 日の期限が延びないように）
        "created_at": time.time() if created_at is None else float(created_at),
        "attempts": int(attempts),
    }
    text = json.dumps(doc, ensure_ascii=False)
    try:
        root = _resume_dir()
        target = root / f"{job.id}.json"
        temp = root / f".{job.id}.{uuid.uuid4().hex[:8]}.tmp"
        fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(text)
            os.replace(temp, target)
        except BaseException:
            temp.unlink(missing_ok=True)
            raise
    except OSError:
        # 記録が書けなくても Job 自体は実行する（再開できないだけ）。
        logger.exception("再開用の記録を書けませんでした (%s)", job.id)


def _unlink_quietly(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
    except OSError:
        logger.warning("再開用の記録を消せませんでした: %s", path.name)


def _read_resume_doc(path: Path) -> dict[str, Any] | None:
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        logger.warning("再開用の記録を読めないので消します: %s", path.name)
        return None
    if not (
        isinstance(doc, dict)
        and isinstance(doc.get("kind"), str)
        and isinstance(doc.get("params"), dict)
        and (doc.get("owner_user_id") is None or isinstance(doc.get("owner_user_id"), int))
        and isinstance(doc.get("title"), str)
        and isinstance(doc.get("created_at"), (int, float))
        and isinstance(doc.get("attempts"), int)
    ):
        logger.warning("再開用の記録の形が違うので消します: %s", path.name)
        return None
    return doc


def _collect_resume_candidates() -> list[dict[str, Any]]:
    """再開できる記録を読む（同期。呼び出し側が to_thread で包む）。

    続けられない記録（未登録の種類・回数や期限を超えたもの・壊れたもの・
    中断で終わっていない Job のもの）はここで消す。起動を止めない。
    """
    try:
        root = _resume_dir()
        entries = sorted(root.iterdir())
    except OSError:
        logger.exception("再開用の記録の置き場を読めません")
        return []
    from app.database import SessionLocal
    from app.models import Job as JobRow

    now = time.time()
    candidates: list[dict[str, Any]] = []
    exhausted: list[tuple[str, str]] = []
    with SessionLocal() as db:
        for path in entries:
            if path.is_symlink():
                _unlink_quietly(path)
                continue
            if path.name.startswith(".") and path.suffix == ".tmp":
                _unlink_quietly(path)  # 書いている途中で落ちた一時ファイル
                continue
            if not path.is_file() or path.suffix != ".json":
                continue
            job_id = path.stem
            if _RESUME_NAME_RE.fullmatch(job_id) is None:
                _unlink_quietly(path)
                logger.warning("再開用の記録の名前が不正なので消します: %s", path.name)
                continue
            try:
                if path.resolve().parent != root.resolve():
                    _unlink_quietly(path)
                    continue
            except OSError:
                _unlink_quietly(path)
                continue
            doc = _read_resume_doc(path)
            if doc is None:
                _unlink_quietly(path)
                continue
            if doc.get("paused"):
                # 利用者が「停止」した Job。自動では再開しない（記録は「再開」のために残す）。
                # ただし 30 日を過ぎた記録は消す（放置された記録が /jobs の走査を増やし続けない）。
                try:
                    stopped_for = now - float(doc["created_at"])
                except (KeyError, TypeError, ValueError):
                    stopped_for = 0.0
                if stopped_for > PAUSED_RESUME_MAX_AGE_SECONDS:
                    _unlink_quietly(path)
                    logger.info("停止したまま期限（30日）を過ぎた Job の記録を消しました: %s", job_id)
                continue
            row = db.get(JobRow, job_id)
            if row is None or row.status != "interrupted":
                _unlink_quietly(path)
                continue
            if doc["kind"] not in _resumable_kinds:
                _unlink_quietly(path)
                logger.info("再開の登録が無い Job の記録を消しました: kind=%s", doc["kind"])
                continue
            if doc["attempts"] >= RESUME_MAX_ATTEMPTS:
                _unlink_quietly(path)
                exhausted.append((
                    job_id,
                    f"自動の再開を {RESUME_MAX_ATTEMPTS} 回試してやめました",
                ))
                continue
            if now - float(doc["created_at"]) > RESUME_MAX_AGE_SECONDS:
                _unlink_quietly(path)
                logger.info("再開の期限（7日）を過ぎた Job の記録を消しました: %s", job_id)
                continue
            candidates.append({"job_id": job_id, **doc})
        if exhausted:
            for job_id, message in exhausted:
                row = db.get(JobRow, job_id)
                if row is not None:
                    row.error = message
            db.commit()
    return candidates


def _close_resume(job_id: str, message: str) -> None:
    """再開の記録を消し、元の Job の行に結果（続きの Job の id・断念の理由）を残す。"""
    from app.database import SessionLocal
    from app.models import Job as JobRow

    with SessionLocal() as db:
        row = db.get(JobRow, job_id)
        if row is not None:
            row.error = message[:2000]
            db.commit()
    _remove_resume_file(job_id)


async def resume_after_startup() -> list[str]:
    """起動の後に裏で、再起動で中断された Job を続きから作り直す（起動は待たせない）。

    lifespan が task として起こす。設定 jobs.auto_resume が false なら何もしない。
    """
    from app.config import get_config

    if not get_config().jobs.auto_resume:
        logger.info("Job の自動再開は設定（jobs.auto_resume）で無効です")
        return []
    try:
        candidates = await asyncio.to_thread(_collect_resume_candidates)
    except Exception:  # noqa: BLE001 - 起動後の再開の失敗でサーバーを止めない
        logger.exception("再開できる Job の記録を読めませんでした")
        return []
    resumed: list[str] = []
    for item in candidates:
        params = item["params"]
        spec = _resumable_kinds.get(item["kind"])
        if spec is None:  # 収集後に登録が消えた場合
            await asyncio.to_thread(_remove_resume_file, item["job_id"])
            continue
        try:
            runner = spec.factory(params)
            base_title = spec.title(params)
            job = create(
                # 画面の実行中の一覧で、再起動の後に続きから始まったことが分かるようにする。
                # 元の題は保存した params から作り直すので、何度再開しても印は重ならない。
                item["kind"], f"{base_title}（再起動のあと続きから）", runner,
                owner_user_id=item["owner_user_id"],
                resume=params,
                _resume_attempts=int(item["attempts"]) + 1,
                _resume_created_at=float(item["created_at"]),
            )
        except Exception as exc:  # noqa: BLE001 - 1 件の失敗で起動後の再開全体を止めない
            logger.exception("再開する Job を作れませんでした (%s)", item["job_id"])
            await asyncio.to_thread(
                _close_resume, item["job_id"],
                f"再開できませんでした: {type(exc).__name__}",
            )
            continue
        await asyncio.to_thread(
            _close_resume, item["job_id"],
            f"再起動のあと Job {job.id} で続きから再開しました",
        )
        resumed.append(job.id)
    if resumed:
        logger.info("中断された Job を %d 件、続きから再開しました", len(resumed))
    return resumed


def create(
    kind: str, title: str, runner: Callable[[Job], Awaitable[Any]],
    owner_user_id: int | None = None,
    *, idempotency_key: str | None = None, priority: int = 0,
    resource: Callable[[str], ResourceRequest] | None = None,
    resume: dict | None = None,
    _resume_attempts: int = 0,
    _resume_created_at: float | None = None,
) -> Job:
    """ジョブをpriority queueへ登録する。同一owner/kind/keyの有効結果は再利用する。

    resume に、再開に要る引数（JSON にできる平らな値だけ。秘密値は入れない）を渡すと、
    再起動で中断されたときに resume_after_startup() が同じ処理を作り直す。
    _resume_attempts は自動再開の回数（通常の作成では 0）。
    """
    if idempotency_key:
        if len(idempotency_key) > 160:
            raise ValueError("idempotency_keyは160文字以内です")
        existing = _find_idempotent(owner_user_id, kind, idempotency_key)
        if existing is not None:
            memory = _jobs.get(existing["id"])
            return memory if memory is not None else _dict_to_job(existing)
        _retire_failed_idempotency(owner_user_id, kind, idempotency_key)
    if resume is not None:
        # 書けない引数（秘密を含めない約束のため、JSON にできるものだけ）は
        # Job を作る前に弾く。後から書けずに再開できない状態を作らない。
        json.dumps(resume, ensure_ascii=False)
    priority = max(-100, min(100, int(priority)))
    job = Job(
        id=uuid.uuid4().hex[:12], kind=kind, title=title, owner_user_id=owner_user_id,
        idempotency_key=idempotency_key, priority=priority,
        resumable=resume is not None,
    )
    resource_request = resource(job.id) if resource is not None else None
    if resource_request is not None and resource_request.job_id != job.id:
        raise ValueError("resource requestのjob_idが作成jobと一致しません")
    if resource_request is not None:
        job.phase = "waiting_resource"
    _jobs[job.id] = job
    # 完了済みジョブが溜まりすぎたら古い順にメモリから破棄（DB には残る）
    finished = [j for j in _jobs.values() if j.status not in ("queued", "running")]
    for old in finished[: max(0, len(_jobs) - MAX_JOBS)]:
        _jobs.pop(old.id, None)
    _db_write(job)  # queued作成時に1回
    if resume is not None:
        _write_resume_file(job, resume, attempts=_resume_attempts, created_at=_resume_created_at)
    _notify_job(job)
    if resource_request is None:
        heapq.heappush(_pending, (-priority, next(_sequence), job.id, runner))
        _dispatch()
    else:
        job.resource_task = asyncio.create_task(
            _admit_resource(job, runner, resource_request, resource_broker)
        )
    return job


def create_external(addon_id: str, title: str, *, owner_user_id: int) -> Job:
    """Create a host-visible job whose execution is owned by an Add-on service."""
    active = sum(
        1 for item in _jobs.values()
        if item.status in {"queued", "running"}
        and item.owner_user_id == owner_user_id
        and addon_owns(item, addon_id)
    )
    if active >= MAX_EXTERNAL_ACTIVE_PER_OWNER:
        raise RuntimeError("同一Add-on userのactive Job上限に達しました")
    job = Job(
        id=uuid.uuid4().hex[:12],
        kind=f"addon.runtime.{addon_id}",
        title=title[:300],
        status="running",
        phase="starting",
        owner_user_id=owner_user_id,
        external_addon_id=addon_id,
    )
    _jobs[job.id] = job
    _db_write(job)
    _notify_job(job)
    return job


def addon_owns(job: Job, addon_id: str) -> bool:
    return (
        job.external_addon_id == addon_id
        or job.kind.startswith(f"addon.runtime.{addon_id}")
        or job.kind.startswith(f"addon.agent_tool.{addon_id}.")
    )


def update_external(
    job: Job,
    *,
    addon_id: str,
    phase: str,
    completed: int | None,
    total: int | None,
    message: str | None,
    wait_reason: str | None,
    terminal_status: str | None,
    result: Any,
    error: str | None,
) -> Job:
    if not addon_owns(job, addon_id):
        raise PermissionError("Add-onが所有するJobではありません")
    if job.status not in {"queued", "running"}:
        raise RuntimeError("Jobはすでに終了しています")
    now = time.monotonic()
    if terminal_status is None and job.last_external_update_at and now - job.last_external_update_at < 0.5:
        raise RuntimeError("progress updateは2Hz以下にしてください")
    previous = job.progress.get("completed")
    if completed is not None and isinstance(previous, int) and completed < previous:
        raise ValueError("progressは単調増加である必要があります")
    if completed is not None and total is not None and completed > total:
        raise ValueError("progress completedはtotal以下にしてください")
    job.phase = phase
    job.wait_reason = wait_reason
    job.progress = {
        "status": phase,
        "completed": completed,
        "total": total,
        "phase": phase,
    }
    if message:
        job.log(message)
    if terminal_status is not None:
        job.status = terminal_status
        job.result = result
        job.error = (error or "")[:2000]
        job.finished_at = time.time()
    job.last_external_update_at = now
    _notify_job(job)
    _db_write(job, terminal_status is not None)
    return job


def cancel_addon(addon_id: str) -> int:
    canceled = 0
    for job in list(_jobs.values()):
        if job.status in {"queued", "running"} and addon_owns(job, addon_id) and cancel(job.id):
            canceled += 1
    return canceled


async def _admit_resource(
    job: Job,
    runner: Callable[[Job], Awaitable[Any]],
    request: ResourceRequest,
    broker: ResourceBroker,
) -> None:
    try:
        job.resource_broker = broker
        job.resource_requirement = request
        status = await broker.submit(request)
        job.resource_request_id = status.request_id
        job.wait_reason = status.reason.value if status.reason else None
        _notify_job(job)
        await asyncio.to_thread(_db_write, job)
        if status.state == RequestState.WAITING:
            status = await _wait_for_resource_updates(job, broker, status.request_id)
        if status.state != RequestState.GRANTED or not status.lease_id:
            if job.status != "canceled":
                job.status = "failed"
                job.error = f"resource admission failed: {status.state.value}"
                job.finished_at = time.time()
                job.wait_reason = status.reason.value if status.reason else None
                _notify_job(job)
                await asyncio.to_thread(_db_write, job, True)
            return
        job.resource_lease_id = status.lease_id
        await broker.activate(status.lease_id)
        job.phase = "starting"
        job.wait_reason = None
        job.resource_renew_task = asyncio.create_task(_renew_resource(job, broker))
        _notify_job(job)
        await asyncio.to_thread(_db_write, job)
        heapq.heappush(_pending, (-job.priority, next(_sequence), job.id, runner))
        _dispatch()
    except asyncio.CancelledError:
        await _cancel_resource_state(job, broker)
        raise
    except Exception as exc:  # Broker failure is isolated to the resource-aware job.
        logger.exception("job resource admission failed (%s)", job.id)
        await _cancel_resource_state(job, broker)
        if job.status != "canceled":
            job.status = "failed"
            job.error = f"resource admission failed: {type(exc).__name__}"[:500]
            job.finished_at = time.time()
            _notify_job(job)
            await asyncio.to_thread(_db_write, job, True)


async def _wait_for_resource_updates(
    job: Job, broker: ResourceBroker, request_id: str,
) -> RequestStatus:
    """Mirror broker reason changes while its terminal wait remains runner-free."""
    terminal = asyncio.create_task(broker.wait(request_id))
    revision = broker.revision
    try:
        while not terminal.done():
            changed = asyncio.create_task(broker.wait_for_revision(revision, 1.0))
            done, _pending = await asyncio.wait(
                (terminal, changed), return_when=asyncio.FIRST_COMPLETED,
            )
            if terminal in done:
                changed.cancel()
                await asyncio.gather(changed, return_exceptions=True)
                break
            revision = changed.result()
            current = await broker.request_status(request_id)
            reason = current.reason.value if current.reason else None
            if reason != job.wait_reason:
                job.wait_reason = reason
                _notify_job(job)
                await asyncio.to_thread(_db_write, job)
        return await terminal
    finally:
        if not terminal.done():
            terminal.cancel()
            await asyncio.gather(terminal, return_exceptions=True)


async def _renew_resource(job: Job, broker: ResourceBroker) -> None:
    try:
        while job.status in ("queued", "running") and job.resource_lease_id:
            await asyncio.sleep(10)
            if job.status in ("queued", "running") and job.resource_lease_id:
                await broker.renew(job.resource_lease_id)
    except asyncio.CancelledError:
        raise
    except LeaseError:
        logger.warning("job resource lease renewal failed (%s)", job.id)


async def _cancel_resource_state(job: Job, broker: ResourceBroker = resource_broker) -> None:
    if job.resource_renew_task is not None and job.resource_renew_task is not asyncio.current_task():
        job.resource_renew_task.cancel()
        await asyncio.gather(job.resource_renew_task, return_exceptions=True)
        job.resource_renew_task = None
    if job.resource_request_id:
        try:
            await broker.cancel_request(job.resource_request_id)
        except BrokerError:
            pass
    if job.resource_lease_id:
        try:
            await broker.release(job.resource_lease_id)
        except LeaseError:
            pass
        job.resource_lease_id = None


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
        result = await runner(job)
        if job.status in ("queued", "running"):
            job.result = result
            job.status = "succeeded"
    except asyncio.CancelledError:
        if job.pause_requested and job.resumable:
            # 利用者の「停止」。途中のファイルと再開の記録を残す（interrupted は終端ではないので
            # 記録は消えない。取消とは区別する: docs/PLAN-model-catalog.md「決めた意味」）。
            job.status = "interrupted"
            job.error = "停止しました（途中のデータを残しています。「再開」で続けられます）"
            job.log(job.error)
        elif _shutdown_started and job.resumable:
            # 利用者の取り消しではなくサーバーの停止。終わったことにせず、
            # 次の起動で続きから再開できるよう記録を残す。
            job.error = "サーバーの停止により中断しました（次回の起動で続きから再開します）"
            job.log(job.error)
        else:
            job.status = "canceled"
            job.error = "キャンセルされました"
    except Exception as e:  # ジョブ失敗は記録して終わり（プロセスは守る）
        if job.status not in ("queued", "running"):
            return
        job.status = "failed"
        if _is_oom_error(e) and job.resource_requirement and job.resource_lease_id:
            lease = (job.resource_broker or resource_broker).leases.get(job.resource_lease_id)
            device = (
                (job.resource_broker or resource_broker).devices.get(lease.device_id)
                if lease else None
            )
            if lease and job.resource_requirement.residency_key:
                (job.resource_broker or resource_broker).telemetry.record_oom(
                    job.resource_requirement.residency_key,
                    lease.device_id,
                    observed_peak_bytes=device.observed_used_bytes if device else lease.reserved_bytes,
                    requested_bytes=lease.reserved_bytes,
                )
            job.error = "resource_oom"
        else:
            job.error = f"{type(e).__name__}: {e}"[:500]
        logger.warning("job %s (%s) failed: %s", job.id, job.kind, job.error)
    finally:
        heartbeat.cancel()
        if job.progress_flush_task is not None:
            job.progress_flush_task.cancel()
            await asyncio.gather(job.progress_flush_task, return_exceptions=True)
        await _cancel_resource_state(job, job.resource_broker or resource_broker)
        job.finished_at = time.time()
        _notify_job(job)
        await asyncio.to_thread(_db_write, job, True)
        _running_count = max(0, _running_count - 1)
        _dispatch()


def _is_oom_error(error: BaseException) -> bool:
    value = str(error).casefold()
    return (
        any(token in value for token in ("out of memory", "out_of_memory", "cannot allocate"))
        or re.search(r"\boom\b", value) is not None
    )


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
    events = data.get("events") or []
    event_sequence = int(data.get("event_count") or len(events))
    return Job(
        id=data["id"], kind=data["kind"], title=data["title"], status=data["status"],
        phase=data.get("phase"), wait_reason=data.get("wait_reason"),
        progress=data.get("progress") or {}, events=events,
        event_offset=max(0, event_sequence - len(events)), event_sequence=event_sequence,
        result=data.get("result"),
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
        "phase": row.phase, "wait_reason": row.wait_reason,
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
        if job.resource_task and not job.resource_task.done():
            job.resource_task.cancel()
        elif job.resource_request_id or job.resource_lease_id:
            asyncio.create_task(_cancel_resource_state(job, job.resource_broker or resource_broker))
        return True
    if job and job.status == "running" and job.task and not job.task.done():
        job.task.cancel()
        return True
    if job and job.status == "running" and job.kind.startswith("addon.runtime."):
        job.status = "canceled"
        job.error = "Control Deckからキャンセルされました"
        job.finished_at = time.time()
        _notify_job(job)
        _db_write(job, True)
        return True
    return False


async def cancel_and_wait(job_id: str) -> bool:
    """Cancel and synchronously reclaim a waiting request/lease for API callers."""
    job = _jobs.get(job_id)
    if job is not None and job.status == "interrupted":
        # 停止していた Job の取り消し。取り消しの契約（docs/PLAN-model-catalog.md
        # 「決めた意味」）に従い、paused の再開の記録を消して終端にする。取り消したのに
        # 再開できる状態が残るのは不整合。途中のファイル（.part）はランタイム側の契約に
        # 任せる（Job 基盤は場所を知らないのでここでは消さない）。
        job.status = "canceled"
        job.error = "停止していたジョブを取り消しました（再開はできません）"
        job.finished_at = time.time()
        _notify_job(job)
        await asyncio.to_thread(_db_write, job, True)   # 終端なので再開の記録も消える
        return True
    if not cancel(job_id):
        return False
    if job is not None and job.resource_task is not None:
        await asyncio.gather(job.resource_task, return_exceptions=True)
    if job is not None:
        await _cancel_resource_state(job, job.resource_broker or resource_broker)
    return True


def cancel_interrupted_row(job_id: str) -> bool:
    """メモリに無い停止中（interrupted）の Job を取り消す（同期。呼び出し側が to_thread で包む）。

    再起動でメモリから消えた停止中の Job（DB の行と paused の再開の記録だけが残っている）
    を取り消す。取り消しの契約（docs/PLAN-model-catalog.md「決めた意味」）に従い、再開の
    記録を消して終端にする。対象が無い・既に終わっているときは False。
    """
    from app.database import SessionLocal
    from app.models import Job as JobRow
    from app.models import utcnow

    with SessionLocal() as db:
        row = db.get(JobRow, job_id)
        if row is None or row.status != "interrupted":
            return False
        row.status = "canceled"
        row.error = "停止していたジョブを取り消しました（再開はできません）"
        row.finished_at = utcnow()
        db.commit()
    _remove_resume_file(job_id)
    return True


# ---- 停止（pause）と手動の再開 ---------------------------------------------------------------
#
# 「停止」と「取り消し」を分ける（2026-11-28、docs/PLAN-model-catalog.md「決めた意味」）。
# 停止は再開の記録と途中のファイルを残し、取り消し（cancel）は記録を消して終端にする。
# 停止の印（paused）のある記録は、起動時の自動再開では拾わない（利用者が止めたものは
# 利用者だけが再開する）。


def _mark_resume_paused(job_id: str, paused: bool) -> None:
    """再開の記録に停止の印を付ける（原子書き込みは _write_resume_file と同じ）。"""
    path = _resume_dir() / f"{job_id}.json"
    doc = _read_resume_doc(path)
    if doc is None:
        return                      # 記録が無い・読めないなら何もしない（stop は諦めるだけ）
    if bool(doc.get("paused")) == paused:
        return
    doc["paused"] = bool(paused)
    text = json.dumps(doc, ensure_ascii=False)
    temp = path.with_name(f".{job_id}.{uuid.uuid4().hex[:8]}.tmp")
    try:
        fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(text)
            os.replace(temp, path)
        except BaseException:
            temp.unlink(missing_ok=True)
            raise
    except OSError:
        logger.exception("再開用の記録に停止の印を付けられませんでした (%s)", job_id)


async def stop(job_id: str) -> str:
    """Job を「停止」する（取り消しと違い、再開の記録と途中のファイルを残す）。

    戻り値は停止後の状態（"interrupted"）。止められない Job（実行中でない・再開できない）
    は ValueError。
    """
    job = _jobs.get(job_id)
    if job is None or not job.resumable or job.status not in ("queued", "running"):
        raise ValueError("この Job は停止できません（実行中の再開できる Job ではありません）")
    job.pause_requested = True
    await asyncio.to_thread(_mark_resume_paused, job_id, True)
    if job.status == "queued":
        # まだ始まっていない: その場で interrupted にする（_dispatch は queued 以外は拾わない）。
        job.status = "interrupted"
        job.error = "停止しました（開始前です。「再開」で続けられます）"
        job.finished_at = time.time()
        _notify_job(job)
        await asyncio.to_thread(_db_write, job, True)
        if job.resource_task and not job.resource_task.done():
            job.resource_task.cancel()
        elif job.resource_request_id or job.resource_lease_id:
            asyncio.create_task(_cancel_resource_state(job, job.resource_broker or resource_broker))
        return job.status
    if job.task is None or job.task.done():
        raise ValueError("この Job は停止できません（実行が終わっています）")
    job.task.cancel()
    # _run_job の CancelledError の処理で状態が interrupted になるのを待つ。
    await asyncio.gather(job.task, return_exceptions=True)
    return job.status


async def resume_stopped(job_id: str) -> Job:
    """停止（または再起動で中断）した Job を、保存した引数から手動で作り直す。

    自動再開（resume_after_startup）との違い: 回数の歯止めを消費せず、元の作成時刻と
    回数をそのまま引き継ぐ。元の Job の行には続きの Job の id を残す。
    """
    # 記録の読み取り〜create〜_close_resume の前に印を付ける。印が無いと、同じ Job の
    # 「再開」を続けて押したときに Job が 2 つ作られる（_close_resume が後から効く）。
    if job_id in _resuming:
        raise ValueError("この Job は再開の処理中です")
    _resuming.add(job_id)
    try:
        return await _resume_stopped_unlocked(job_id)
    finally:
        _resuming.discard(job_id)


async def _resume_stopped_unlocked(job_id: str) -> Job:
    path = _resume_dir() / f"{job_id}.json"
    doc = await asyncio.to_thread(_read_resume_doc, path)
    if doc is None:
        raise ValueError("再開できる記録がありません（取り消されたか終わっています）")
    memory = _jobs.get(job_id)
    if memory is not None:
        if memory.status != "interrupted":
            raise ValueError(f"この Job は再開できません（{memory.status}）")
    else:
        row = await asyncio.to_thread(_db_get, job_id)
        if row is None or row.get("status") != "interrupted":
            raise ValueError("再開できる Job ではありません（中断の記録が見つかりません）")
    spec = _resumable_kinds.get(str(doc["kind"]))
    if spec is None:
        raise ValueError("この Job の種類は再開に対応していません")
    try:
        runner = spec.factory(doc["params"])
    except Exception as exc:  # noqa: BLE001 - 保存した引数から作れないときは利用者へ理由を返す
        raise ValueError(f"再開できませんでした: {exc}") from exc
    job = create(
        str(doc["kind"]), spec.title(doc["params"]), runner,
        owner_user_id=doc.get("owner_user_id"),
        resume=doc["params"],
        _resume_attempts=int(doc["attempts"]),
        _resume_created_at=float(doc["created_at"]),
    )
    await asyncio.to_thread(_close_resume, job_id, f"Job {job.id} で続きから再開しました")
    return job


def resume_record_ids() -> set[str]:
    """再開の記録が残っている Job の id（API の can_resume 用。同期・呼び出し側で to_thread）。"""
    try:
        return {p.stem for p in _resume_dir().iterdir()
                if p.suffix == ".json" and not p.name.startswith(".")}
    except OSError:
        return set()


def visible_to(job: Job | dict, owner_user_id: int) -> bool:
    owner = job.owner_user_id if isinstance(job, Job) else job.get("owner_user_id")
    return owner is None or owner == owner_user_id


async def wait_events(job: Job, cursor: int, timeout: float = 25.0) -> int:
    """pollせず通知eventでイベント追加/状態変化を待つ。"""
    if job.event_sequence <= cursor and job.status in ("queued", "running"):
        job.changed.clear()
        if job.event_sequence <= cursor and job.status in ("queued", "running"):
            try:
                await asyncio.wait_for(job.changed.wait(), timeout=timeout)
            except TimeoutError:
                pass
    return job.event_sequence


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
    再開できる Job（register_resumable に登録した種類で resume= 付きで作られたもの）は、
    この後に resume_after_startup() が保存した引数から作り直す。
    """
    from app.database import SessionLocal
    from app.models import Job as JobRow
    from app.models import utcnow

    db = SessionLocal()
    try:
        rows = db.query(JobRow).filter(JobRow.status.in_(("queued", "running"))).all()
        chat_jobs = [row.id for row in rows if row.kind == "chat.completion"]
        for row in rows:
            row.status = "interrupted"
            row.error = row.error or "バックエンド再起動により中断されました"
            row.finished_at = utcnow()
        if rows:
            db.commit()
            logger.info("%d 件の実行中ジョブを interrupted としてマーク", len(rows))
            if chat_jobs:
                from app.alerts import chat_events

                chat_events.announce_interrupted(chat_jobs)   # OpenCode の回だったものを知らせる（#561）
        return len(rows)
    finally:
        db.close()
