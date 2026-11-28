"""ジョブ基盤の DB 永続化・再起動復元・API のテスト。"""
import asyncio
import time

import pytest

from app.jobs import service as jobs


def _run(coro):
    async def run_and_finish_writes():
        try:
            return await coro
        finally:
            # Terminal status precedes the final DB write and runner-slot
            # release. Closing this per-test loop at that point cancels cleanup
            # and leaks the process-global slot count into the next test.
            loop = asyncio.get_running_loop()
            tasks = [task for job in list(jobs._jobs.values())
                     if job.status not in {"queued", "running"}
                     for task in (job.task, job.resource_task)
                     if task is not None and task.get_loop() is loop and not task.done()]
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)

    return asyncio.run(run_and_finish_writes())


def test_job_persisted_to_db(client):
    """ジョブ作成→完了が DB に記録され、get_any で再取得できる。"""
    async def work(job):
        job.set_progress("処理中", 1, 2)
        job.log("開始")
        await asyncio.sleep(0.05)
        return {"answer": 42}

    async def scenario():
        job = jobs.create("test.persist", "永続テスト", work, owner_user_id=None)
        for _ in range(40):
            await asyncio.sleep(0.05)
            if job.status != "running":
                break
        return job.id

    job_id = _run(scenario())
    # DB からも取得できる（メモリを消しても残る）
    jobs._jobs.pop(job_id, None)  # メモリから消す
    got = _run(jobs.get_any(job_id))
    assert got is not None
    assert got["status"] == "succeeded"
    assert got["result"]["answer"] == 42
    assert got.get("persisted") is True


def test_bounded_event_journal_uses_monotonic_cursor():
    """300件で先頭を捨ててもcursorが止まらず、新規deltaを購読できる。"""
    job = jobs.Job(id="bounded", kind="chat.completion", title="long answer")
    for number in range(jobs.MAX_EVENTS + 50):
        job.log("delta", delta=str(number))

    assert len(job.events) == jobs.MAX_EVENTS
    assert job.event_offset == 50
    assert job.event_sequence == jobs.MAX_EVENTS + 50
    retained, cursor, truncated = job.events_since(0)
    assert truncated is True
    assert retained[0]["delta"] == "50"
    assert cursor == 350

    job.log("delta", delta="next")
    fresh, next_cursor, truncated = job.events_since(cursor)
    assert truncated is False
    assert [event["delta"] for event in fresh] == ["next"]
    assert next_cursor == 351


def test_recover_on_startup_marks_interrupted():
    """DB に running のまま残ったジョブを起動時に interrupted へ。"""
    from app.database import SessionLocal
    from app.jobs import service as jobs
    from app.models import Job as JobRow

    db = SessionLocal()
    try:
        db.add(JobRow(id="stuck-job-01", kind="test.stuck", title="放置", status="running"))
        db.commit()
    finally:
        db.close()
    n = jobs.recover_on_startup()
    assert n >= 1
    db = SessionLocal()
    try:
        row = db.get(JobRow, "stuck-job-01")
        assert row.status == "interrupted"
        assert row.finished_at is not None
    finally:
        db.close()


def test_jobs_api_list_and_get(admin_client):
    """DB に残ったジョブが API 一覧・詳細で見える（メモリに無くても）。"""
    from app.database import SessionLocal
    from app.models import Job as JobRow
    from app.models import utcnow

    db = SessionLocal()
    try:
        db.add(JobRow(id="api-job-01", kind="test.api", title="API履歴", status="succeeded",
                      result_json='{"ok": true}', finished_at=utcnow()))
        db.commit()
    finally:
        db.close()
    r = admin_client.get("/api/v1/jobs?kind=test.api")
    assert r.status_code == 200
    assert any(j["id"] == "api-job-01" for j in r.json())
    r = admin_client.get("/api/v1/jobs/api-job-01")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "succeeded" and body["result"]["ok"] is True
    # 存在しない
    assert admin_client.get("/api/v1/jobs/nope-xxxx").status_code == 404


def test_job_failure_recorded(client):
    from app.jobs import service as jobs

    async def boom(job):
        raise ValueError("わざと失敗")

    async def scenario():
        job = jobs.create("test.fail", "失敗テスト", boom)
        for _ in range(40):
            await asyncio.sleep(0.05)
            if job.status != "running":
                break
        return job.id

    job_id = _run(scenario())
    got = _run(jobs.get_any(job_id))
    assert got["status"] == "failed"
    assert "わざと失敗" in got["error"]


def test_idempotency_priority_and_queued_cancel(client, monkeypatch):
    from app.jobs import service as jobs
    from app.database import SessionLocal
    from app.models import User

    with SessionLocal() as db:
        owner_id = db.query(User).filter(User.username == "admin").one().id

    async def scenario():
        monkeypatch.setattr(jobs, "MAX_CONCURRENT", 1)
        order = []
        gate = asyncio.Event()

        async def blocking(job):
            order.append("blocking")
            await gate.wait()

        async def named(name):
            async def run(job):
                order.append(name)
                return name
            return run

        first = jobs.create("test.queue", "block", blocking, owner_user_id=owner_id)
        low = jobs.create("test.queue", "low", await named("low"), owner_user_id=owner_id, priority=-5)
        high = jobs.create("test.queue", "high", await named("high"), owner_user_id=owner_id, priority=10)
        canceled = jobs.create("test.queue", "cancel", await named("cancel"), owner_user_id=owner_id)
        assert canceled.status == "queued" and jobs.cancel(canceled.id)
        gate.set()
        for _ in range(1000):   # 最大 10 秒（関門の全体実行の負荷でも待てるように。速ければすぐ抜ける）
            if high.status == low.status == "succeeded":
                break
            await asyncio.sleep(0.01)
        assert order == ["blocking", "high", "low"]
        assert canceled.status == "canceled"

        async def once(job):
            return {"ok": True}

        idem1 = jobs.create("test.idem", "one", once, owner_user_id=owner_id, idempotency_key="same-request")
        while idem1.status in ("queued", "running"):
            await asyncio.sleep(0.01)
        idem2 = jobs.create("test.idem", "two", once, owner_user_id=owner_id, idempotency_key="same-request")
        assert idem2.id == idem1.id and idem2.status == "succeeded"

    _run(scenario())


def test_waiting_resource_job_does_not_consume_runner_slot_and_cancel_reclaims_request(
    client, monkeypatch,
):
    from app.jobs import service as jobs
    from app.resources.broker import ResourceBroker
    from app.resources.devices import fake_devices
    from app.resources.schema import ResourceRequest

    broker = ResourceBroker(fake_devices(100))
    monkeypatch.setattr(jobs, "resource_broker", broker)
    monkeypatch.setattr(jobs, "MAX_CONCURRENT", 1)

    def requirement(owner: str, job_id: str) -> ResourceRequest:
        return ResourceRequest.model_validate({
            "owner": owner,
            "job_id": job_id,
            "device": "gpu0",
            "vram": {
                "resident_bytes": 100,
                "execution_peak_bytes": 100,
                "cold_load_peak_bytes": 100,
                "headroom_bytes": 0,
                "confidence": "measured",
            },
            "compute_mode": "exclusive-required",
            "class": "interactive",
            "max_wait_sec": 60,
        })

    async def scenario():
        held = await broker.submit(requirement("internal:holder", "holder"))
        assert held.lease_id

        ran = asyncio.Event()

        async def gpu_runner(_job):
            raise AssertionError("resource waiter must not run")

        async def cpu_runner(_job):
            ran.set()
            return "cpu"

        waiting = jobs.create(
            "test.resource", "GPU wait", gpu_runner,
            resource=lambda job_id: requirement("internal:media", job_id),
        )
        cpu = jobs.create("test.cpu", "CPU", cpu_runner)
        await asyncio.wait_for(ran.wait(), timeout=10)
        for _ in range(1000):   # 最大 10 秒（関門の全体実行の負荷でも待てるように。速ければすぐ抜ける）
            if waiting.wait_reason:
                break
            await asyncio.sleep(0.01)
        assert waiting.status == "queued"
        assert waiting.phase == "waiting_resource"
        assert waiting.wait_reason == "device_busy_exclusive"
        assert cpu.status in ("running", "succeeded")
        request_id = waiting.resource_request_id
        assert request_id
        assert await jobs.cancel_and_wait(waiting.id)
        assert waiting.status == "canceled"
        assert (await broker.request_status(request_id)).state.value == "canceled"
        await broker.release(held.lease_id)
        return waiting.id

    waiting_id = _run(scenario())
    persisted = _run(jobs.get_any(waiting_id))
    assert persisted["phase"] == "waiting_resource"
    assert persisted["wait_reason"] == "device_busy_exclusive"


def test_resource_job_progress_is_phase_bound_monotonic_and_coalesced(monkeypatch):
    from app.jobs import service as jobs
    from app.resources.broker import ResourceBroker
    from app.resources.devices import fake_devices
    from app.resources.schema import ResourceRequest

    broker = ResourceBroker(fake_devices(100))
    monkeypatch.setattr(jobs, "resource_broker", broker)

    def requirement(job_id: str) -> ResourceRequest:
        return ResourceRequest.model_validate({
            "owner": "internal:progress",
            "job_id": job_id,
            "device": "gpu0",
            "vram": {
                "resident_bytes": 1, "execution_peak_bytes": 1,
                "cold_load_peak_bytes": 1, "headroom_bytes": 0,
                "confidence": "measured",
            },
            "compute_mode": "shared-safe",
            "class": "workflow",
        })

    async def scenario():
        observations = {}

        async def runner(job):
            before = job.revision
            for completed in range(1, 11):
                job.set_progress("generating", completed, 10)
            observations["immediate"] = job.revision - before
            try:
                job.set_progress("invalid", 9, 10)
            except ValueError:
                observations["monotonic"] = True
            await asyncio.sleep(0.55)
            observations["coalesced"] = job.revision - before
            return "ok"

        job = jobs.create(
            "test.resource-progress", "progress", runner,
            resource=lambda job_id: requirement(job_id),
        )
        while job.status in ("queued", "running"):
            await asyncio.sleep(0.01)
        return job, observations

    job, observations = _run(scenario())
    assert job.status == "succeeded"
    assert job.progress == {
        "status": "generating", "completed": 10, "total": 10, "phase": "generating"
    }
    assert observations == {"immediate": 1, "monotonic": True, "coalesced": 2}

    no_phase = jobs.Job("no-phase", "test", "test", resource_broker=broker)
    with pytest.raises(ValueError, match="phase"):
        no_phase.set_progress("x", 1, 1)


def test_resource_job_oom_is_normalized_profiled_and_not_retried(monkeypatch):
    from app.jobs import service as jobs
    from app.resources.broker import ResourceBroker
    from app.resources.devices import fake_devices
    from app.resources.schema import ResourceRequest

    broker = ResourceBroker(fake_devices(100))
    monkeypatch.setattr(jobs, "resource_broker", broker)

    def requirement(job_id: str) -> ResourceRequest:
        return ResourceRequest.model_validate({
            "owner": "internal:oom",
            "job_id": job_id,
            "device": "gpu0",
            "vram": {
                "resident_bytes": 80, "execution_peak_bytes": 80,
                "cold_load_peak_bytes": 80, "headroom_bytes": 0,
                "confidence": "estimated",
            },
            "compute_mode": "shared-safe",
            "class": "background",
            "residency_key": "runtime:risky-model",
        })

    async def scenario():
        calls = 0

        async def runner(_job):
            nonlocal calls
            calls += 1
            raise RuntimeError("HIP out of memory")

        job = jobs.create(
            "test.resource-oom", "oom", runner,
            resource=lambda job_id: requirement(job_id),
        )
        while job.status in ("queued", "running"):
            await asyncio.sleep(0.01)
        return job, calls, await broker.snapshot()

    job, calls, snapshot = _run(scenario())
    assert job.status == "failed" and job.error == "resource_oom"
    assert calls == 1
    assert snapshot["telemetry"]["counters"]["oom.incident"] == 1
    assert snapshot["telemetry"]["oom_profiles"][0]["recommended_bytes"] == 88
    assert snapshot["leases"][0]["state"] == "released"


def test_two_exclusive_resource_jobs_execute_serially(monkeypatch):
    from app.jobs import service as jobs
    from app.resources.broker import ResourceBroker
    from app.resources.devices import fake_devices
    from app.resources.schema import ResourceRequest

    broker = ResourceBroker(fake_devices(100))
    monkeypatch.setattr(jobs, "resource_broker", broker)
    monkeypatch.setattr(jobs, "MAX_CONCURRENT", 2)

    def requirement(job_id: str) -> ResourceRequest:
        return ResourceRequest.model_validate({
            "owner": f"internal:{job_id}", "job_id": job_id, "device": "gpu0",
            "vram": {
                "resident_bytes": 100, "execution_peak_bytes": 100,
                "cold_load_peak_bytes": 100, "headroom_bytes": 0,
                "confidence": "measured",
            },
            "compute_mode": "exclusive-required", "class": "background",
        })

    async def scenario():
        first_gate = asyncio.Event()
        order = []

        async def first_runner(_job):
            order.append("first-start")
            await first_gate.wait()
            order.append("first-end")

        async def second_runner(_job):
            order.append("second-start")

        first = jobs.create(
            "test.gpu", "first", first_runner,
            resource=lambda job_id: requirement(job_id),
        )
        second = jobs.create(
            "test.gpu", "second", second_runner,
            resource=lambda job_id: requirement(job_id),
        )
        for _ in range(1000):   # 最大 10 秒（関門の全体実行の負荷でも待てるように。速ければすぐ抜ける）
            # status が running になってから runner が動き出すまでに間がある。runner が始まったことも待つ
            if first.status == "running" and second.wait_reason and order:
                break
            await asyncio.sleep(0.01)
        assert first.status == "running"
        assert second.status == "queued" and second.phase == "waiting_resource"
        assert order == ["first-start"]
        first_gate.set()
        for _ in range(1000):   # 最大 10 秒（関門の全体実行の負荷でも待てるように。速ければすぐ抜ける）
            if first.status == second.status == "succeeded":
                break
            await asyncio.sleep(0.01)
        return first, second, order, await broker.snapshot()

    first, second, order, snapshot = _run(scenario())
    assert first.status == second.status == "succeeded"
    assert order == ["first-start", "first-end", "second-start"]
    assert snapshot["devices"][0]["lease_reserved_bytes"] == 0


def test_jobs_api_hides_other_owners_and_streams_snapshot(admin_client):
    from app.bootstrap import create_admin
    from app.database import SessionLocal
    from app.jobs import service as jobs
    from app.models import User

    with SessionLocal() as db:
        admin_id = db.query(User).filter(User.username == "admin").one().id
        other = db.query(User).filter(User.username == "job-other-user").one_or_none()
        if other is None:
            create_admin(db, "job-other-user", "test-password-456")
            other = db.query(User).filter(User.username == "job-other-user").one()
        other_id = other.id

    async def done(job):
        return {"ok": True}

    async def scenario():
        own = jobs.create("test.owner", "own", done, owner_user_id=admin_id)
        foreign = jobs.create("test.owner", "foreign", done, owner_user_id=other_id)
        while own.status in ("queued", "running") or foreign.status in ("queued", "running"):
            await asyncio.sleep(0.01)
        return own.id, foreign.id

    own_id, foreign_id = _run(scenario())
    listed = admin_client.get("/api/v1/jobs?kind=test.owner").json()
    assert any(item["id"] == own_id for item in listed)
    assert not any(item["id"] == foreign_id for item in listed)
    assert admin_client.get(f"/api/v1/jobs/{foreign_id}").status_code == 404
    with admin_client.websocket_connect("/api/v1/jobs/stream?kind=test.owner") as websocket:
        snapshot = websocket.receive_json()
        assert snapshot["type"] == "snapshot"
        ids = {item["id"] for item in snapshot["jobs"]}
        assert own_id in ids and foreign_id not in ids


# ---------------------------------------------------------------------------
# 再起動の後の自動再開（m3-spec.md 2026-11-26）
# ---------------------------------------------------------------------------


def _use_tmp_resume_dir(monkeypatch, tmp_path):
    """再開用の記録の置き場を、本番の data_dir ではなく試験の tmp へ向ける。"""
    root = tmp_path / "resumable"

    def resume_dir():
        root.mkdir(parents=True, exist_ok=True)
        return root

    monkeypatch.setattr(jobs, "_resume_dir", resume_dir)
    return root


def _admin_id() -> int:
    from app.database import SessionLocal
    from app.models import User

    with SessionLocal() as db:
        return db.query(User).filter(User.username == "admin").one().id


def _seed_interrupted(
    root, tmp_path, *, kind: str, params: dict, owner_user_id: int | None,
    attempts: int = 0, created_at: float | None = None, title: str = "続きの試験",
    status: str = "interrupted", job_id: str | None = None, paused: bool = False,
) -> str:
    """再起動で中断された Job の行と、再開用の記録を用意する（再起動の直前の状態）。"""
    import json
    import uuid

    from app.database import SessionLocal
    from app.models import Job as JobRow

    job_id = job_id or f"resume-{uuid.uuid4().hex[:10]}"
    with SessionLocal() as db:
        db.add(JobRow(id=job_id, kind=kind, title=title, status=status,
                      owner_user_id=owner_user_id,
                      error="バックエンド再起動により中断されました"))
        db.commit()
    root.mkdir(parents=True, exist_ok=True)
    (root / f"{job_id}.json").write_text(json.dumps({
        "kind": kind, "params": params, "owner_user_id": owner_user_id,
        "title": title, "created_at": time.time() if created_at is None else created_at,
        "attempts": attempts, "paused": paused,
    }, ensure_ascii=False), encoding="utf-8")
    return job_id


def _job_row(job_id: str):
    from app.database import SessionLocal
    from app.models import Job as JobRow

    with SessionLocal() as db:
        return db.get(JobRow, job_id)


def test_resumable_job_saves_only_resume_params_privately(client, monkeypatch, tmp_path):
    """目的: 再開できる Job の作成で、再開に要る引数だけを 0600 の JSON に残すこと。

    根拠: 再起動で中断された取得などの Job を続きから作り直す（m3-spec.md 2026-11-26）。
    秘密値（HF のトークンなど）を混ぜないため、保存するのは呼び出し側が渡した params だけ
    にする（設定から取る秘密は含めない）。
    """
    import json

    root = _use_tmp_resume_dir(monkeypatch, tmp_path)
    params = {"alias": "m1", "files": ["a.gguf"], "register": None}

    async def scenario():
        gate = asyncio.Event()

        async def run(job):
            await gate.wait()
            return {}

        job = jobs.create("test.resume", "続きの試験", run, owner_user_id=None,
                          resume=params)
        path = root / f"{job.id}.json"
        assert path.exists()
        assert path.stat().st_mode & 0o777 == 0o600
        doc = json.loads(path.read_text(encoding="utf-8"))
        created_at = doc.pop("created_at")
        assert isinstance(created_at, float)
        assert doc == {
            "kind": "test.resume", "params": params, "owner_user_id": None,
            "title": "続きの試験", "attempts": 0,
        }
        gate.set()
        while job.status in ("queued", "running"):
            await asyncio.sleep(0.01)

    _run(scenario())


@pytest.mark.parametrize("outcome", ["succeeded", "failed"])
def test_finished_resumable_job_forgets_record(client, monkeypatch, tmp_path, outcome):
    """目的: 成功・失敗で終わった Job の再開用の記録を消すこと。

    根拠: 失敗した取得を再起動のたびに繰り返さない（m3-spec.md 2026-11-26）。
    """
    root = _use_tmp_resume_dir(monkeypatch, tmp_path)

    async def scenario():
        async def run(job):
            if outcome == "failed":
                raise ValueError("わざと失敗")
            return {}

        job = jobs.create("test.resume", "終わる試験", run, resume={"n": 1})
        path = root / f"{job.id}.json"
        assert path.exists()
        while job.status in ("queued", "running"):
            await asyncio.sleep(0.01)
        assert job.status == outcome
        assert not path.exists()

    _run(scenario())


def test_canceled_resumable_job_is_not_resumed(client, monkeypatch, tmp_path):
    """目的: 利用者が止めた Job の記録は消し、再起動の後に勝手に再開しないこと。

    根拠: 「止めたものは再開しない」（m3-spec.md 2026-11-26）。操作の意図を再起動で覆さない。
    """
    root = _use_tmp_resume_dir(monkeypatch, tmp_path)
    monkeypatch.setattr(jobs, "MAX_CONCURRENT", 0)
    monkeypatch.setattr(jobs, "_shutdown_started", False)

    async def run(job):
        return {}

    job = jobs.create("test.resume", "止める試験", run, resume={"n": 1})
    path = root / f"{job.id}.json"
    assert path.exists()
    assert jobs.cancel(job.id)
    assert not path.exists()
    assert _run(jobs.resume_after_startup()) == []


def test_stop_keeps_the_record_and_resume_starts_a_new_job(client, monkeypatch, tmp_path):
    """目的: 「停止」が再開の記録と停止の印を残し、「再開」が同じ引数で新しい Job を作ること。

    根拠: 利用者の要望（2026-11-28）で停止と取り消しの意味を分けた。停止で記録を消すと
    続きから再開できず、取り消しと同じになる（docs/PLAN-model-catalog.md「決めた意味」）。
    """
    import json

    root = _use_tmp_resume_dir(monkeypatch, tmp_path)
    monkeypatch.setattr(jobs, "_shutdown_started", False)
    started = asyncio.Event()

    def factory(params):
        async def run(job):
            started.set()
            await asyncio.sleep(30)

        return run

    monkeypatch.setitem(jobs._resumable_kinds, "test.resume",
                        jobs._ResumableKind(factory, lambda params: "続き"))

    async def scenario():
        job = jobs.create("test.resume", "止める", factory({}), resume={"alias": "m1"})
        await asyncio.wait_for(started.wait(), 5)
        assert await jobs.stop(job.id) == "interrupted"
        assert job.status == "interrupted"
        doc = json.loads((root / f"{job.id}.json").read_text(encoding="utf-8"))
        assert doc["paused"] is True and doc["params"] == {"alias": "m1"}
        assert job.id in jobs.resume_record_ids()

        # 停止の印が付いた記録は、起動時の自動再開では拾わない（利用者だけが再開する）。
        assert await jobs.resume_after_startup() == []

        started.clear()
        new_job = await jobs.resume_stopped(job.id)
        assert new_job.id != job.id and new_job.title == "続き"
        await asyncio.wait_for(started.wait(), 5)
        assert not (root / f"{job.id}.json").exists()      # 元の記録は次の Job へ引き継いだ
        assert (root / f"{new_job.id}.json").exists()
        assert await jobs.stop(new_job.id) == "interrupted"
        return job.id, new_job.id

    old_id, new_id = _run(scenario())
    row = _job_row(old_id)
    assert row.status == "interrupted"
    assert f"Job {new_id} で続きから再開しました" in row.error


def test_resume_stopped_refuses_a_concurrent_second_request(client, monkeypatch):
    """目的: 同じ Job の「再開」を同時に押しても、Job を 2 つ作らないこと。

    根拠: run4 のレビュー（minor）「resume の読み取り〜作成〜記録の削除が非原子的で、
    同時に押すと Job が二重に作られ得る」。再開は数 GB の取り直しを伴うので、二重に走らせない。
    """
    started = asyncio.Event()
    release = asyncio.Event()

    async def fake_unlocked(job_id):
        started.set()
        await release.wait()
        return "resumed"

    monkeypatch.setattr(jobs, "_resume_stopped_unlocked", fake_unlocked)

    async def scenario():
        first = asyncio.create_task(jobs.resume_stopped("job-concurrent"))
        await asyncio.wait_for(started.wait(), 5)
        with pytest.raises(ValueError, match="再開の処理中"):
            await jobs.resume_stopped("job-concurrent")
        release.set()
        assert await first == "resumed"
        assert "job-concurrent" not in jobs._resuming

    _run(scenario())


def test_paused_resume_record_is_dropped_after_the_retention(client, monkeypatch, tmp_path):
    """目的: 停止したまま 30 日を過ぎた Job の再開の記録を、起動時の整理で消すこと。

    根拠: run4 の再レビュー（minor）「paused の記録を自動再開から外すだけで削除しないため、
    停止したまま放置すると /jobs の走査（resume_record_ids）の費用が増え続ける」。
    新しい記録（再開できる）は残す。
    """
    root = _use_tmp_resume_dir(monkeypatch, tmp_path)
    old = _seed_interrupted(
        root, tmp_path, kind="test.resume", params={"n": 1}, owner_user_id=None,
        created_at=time.time() - jobs.PAUSED_RESUME_MAX_AGE_SECONDS - 60, paused=True)
    fresh = _seed_interrupted(
        root, tmp_path, kind="test.resume", params={"n": 1}, owner_user_id=None, paused=True)

    assert jobs._collect_resume_candidates() == []
    assert not (root / f"{old}.json").exists()
    assert (root / f"{fresh}.json").exists()


def test_job_api_offers_resume_only_for_interrupted_jobs_with_a_record(
        admin_client, monkeypatch, tmp_path):
    """目的: 一覧・詳細の can_resume が、中断していて再開の記録が残る Job だけ True になること。

    根拠: 画面は can_resume で「再開」を出す（docs/PLAN-model-catalog.md）。終わった Job や
    取り消した Job に出すと、押しても 409 になる。
    """
    root = _use_tmp_resume_dir(monkeypatch, tmp_path)
    stopped = _seed_interrupted(root, tmp_path, kind="test.resume",
                                params={"n": 1}, owner_user_id=None)
    finished = _seed_interrupted(root, tmp_path, kind="test.resume",
                                 params={"n": 1}, owner_user_id=None, status="succeeded")

    rows = {row["id"]: row for row in admin_client.get("/api/v1/jobs?kind=test.resume").json()}
    assert rows[stopped]["can_resume"] is True
    assert rows[finished]["can_resume"] is False
    assert admin_client.get(f"/api/v1/jobs/{stopped}").json()["can_resume"] is True
    assert admin_client.get(f"/api/v1/jobs/{finished}").json()["can_resume"] is False

    # 取り消す（記録を消す）と、再開の対象から外れる。
    jobs._remove_resume_file(stopped)
    assert admin_client.get(f"/api/v1/jobs/{stopped}").json()["can_resume"] is False


def test_shutdown_does_not_cancel_resumable_job(client, monkeypatch, tmp_path):
    """目的: サーバーの停止で task が cancel されても、再開できる Job を「キャンセル」に
    せず、再開用の記録を残すこと。

    根拠: 丁寧な停止（systemd の restart）では asyncio が loop を閉じる前に残りの task を
    cancel する。取り消しと同じ扱いにすると、再起動のたびに再開の対象が消える
    （m3-spec.md 2026-11-26「再起動させられても途中から再開」）。
    """
    root = _use_tmp_resume_dir(monkeypatch, tmp_path)
    monkeypatch.setattr(jobs, "_shutdown_started", True)

    async def scenario():
        gate = asyncio.Event()

        async def run(job):
            await gate.wait()
            return {}

        job = jobs.create("test.resume", "停止の試験", run, resume={"n": 1})
        path = root / f"{job.id}.json"
        while job.status != "running":
            await asyncio.sleep(0.01)
        job.task.cancel()
        await asyncio.gather(job.task, return_exceptions=True)
        assert job.status != "canceled"
        assert path.exists()
        # 試験の後片付け（次の試験の一覧に残さない）
        job.status = "canceled"
        await asyncio.to_thread(jobs._db_write, job, True)

    _run(scenario())


def test_startup_resumes_interrupted_job_with_factory_and_owner(client, monkeypatch, tmp_path):
    """目的: 起動後に、中断された Job を保存した引数から作り直すこと。

    根拠: 再起動の後に続きから再開する（m3-spec.md 2026-11-26）。factory が呼ばれ、
    持ち主・題を引き継ぎ、元の Job の行に続きの Job の id が残ること。
    """
    import json

    root = _use_tmp_resume_dir(monkeypatch, tmp_path)
    owner = _admin_id()
    seen = {}
    gate = asyncio.Event()

    def factory(params):
        seen["params"] = params

        async def run(job):
            await gate.wait()
            return {"ok": True}

        return run

    monkeypatch.setitem(
        jobs._resumable_kinds, "test.resume",
        jobs._ResumableKind(factory, lambda params: f"再開: {params['alias']}"),
    )
    old_id = _seed_interrupted(root, tmp_path, kind="test.resume",
                               params={"alias": "m1"}, owner_user_id=owner)

    async def scenario():
        resumed = await jobs.resume_after_startup()
        assert len(resumed) == 1
        new_id = resumed[0]
        assert seen["params"] == {"alias": "m1"}
        new_job = jobs.get(new_id)
        assert new_job is not None
        assert new_job.owner_user_id == owner
        assert new_job.title == "再開: m1（再起動のあと続きから）"
        new_doc = json.loads((root / f"{new_id}.json").read_text(encoding="utf-8"))
        assert new_doc["attempts"] == 1
        assert not (root / f"{old_id}.json").exists()
        gate.set()
        while new_job.status in ("queued", "running"):
            await asyncio.sleep(0.01)
        return new_id

    new_id = _run(scenario())
    row = _job_row(old_id)
    assert row.status == "interrupted"
    assert f"Job {new_id} で続きから再開しました" in row.error


def test_startup_stops_after_three_resume_attempts(client, monkeypatch, tmp_path):
    """目的: 自動の再開を 3 回試したら、それ以上作り直さず理由を Job に残すこと。

    根拠: 何度も落ちる種類で、再起動のたびに取得をやり直し続けない（m3-spec.md 2026-11-26）。
    """
    root = _use_tmp_resume_dir(monkeypatch, tmp_path)
    called = []

    def factory(params):
        called.append(params)

        async def run(job):
            return {}

        return run

    monkeypatch.setitem(jobs._resumable_kinds, "test.resume",
                        jobs._ResumableKind(factory, lambda params: "再開"))
    old_id = _seed_interrupted(root, tmp_path, kind="test.resume",
                               params={"alias": "m1"}, owner_user_id=None, attempts=3)

    assert _run(jobs.resume_after_startup()) == []
    assert called == []
    assert not (root / f"{old_id}.json").exists()
    assert _job_row(old_id).error == "自動の再開を 3 回試してやめました"


def test_resumed_record_keeps_the_first_created_at(client, monkeypatch, tmp_path):
    """目的: 自動で作り直した Job の記録は、最初に作った時刻を引き継ぐこと。
    根拠: 再開のたびに時刻を書き直すと、7 日の期限が延び続ける（2026-11-27 Opus の監査）。"""
    import json

    root = _use_tmp_resume_dir(monkeypatch, tmp_path)

    async def scenario():
        async def run(job):
            await asyncio.Event().wait()

        job = jobs.create("test.resume", "時刻の試験", run, resume={"n": 1},
                          _resume_attempts=1, _resume_created_at=1000.0)
        doc = json.loads((root / f"{job.id}.json").read_text(encoding="utf-8"))
        assert doc["created_at"] == 1000.0
        assert doc["attempts"] == 1
        assert jobs.cancel(job.id)
        await asyncio.gather(*( [job.task] if job.task else []), return_exceptions=True)

    _run(scenario())


def test_startup_does_not_resume_record_older_than_seven_days(client, monkeypatch, tmp_path):
    """目的: 作られてから 7 日を過ぎた Job は再開せず、記録を消すこと。

    根拠: 古い取得を、状況が変わった後に勝手にやり直さない（m3-spec.md 2026-11-26）。
    """
    root = _use_tmp_resume_dir(monkeypatch, tmp_path)
    called = []

    def factory(params):
        called.append(params)

        async def run(job):
            return {}

        return run

    monkeypatch.setitem(jobs._resumable_kinds, "test.resume",
                        jobs._ResumableKind(factory, lambda params: "再開"))
    old_id = _seed_interrupted(
        root, tmp_path, kind="test.resume", params={"alias": "m1"}, owner_user_id=None,
        created_at=time.time() - 8 * 24 * 60 * 60,
    )

    assert _run(jobs.resume_after_startup()) == []
    assert called == []
    assert not (root / f"{old_id}.json").exists()


def test_startup_resume_can_be_disabled_by_config(client, monkeypatch, tmp_path):
    """目的: 設定 jobs.auto_resume=false で、起動後の自動再開を止められること。

    根拠: 自動再開を切る手段を用意する（m3-spec.md 2026-11-26）。記録は消さず、次に
    有効にしたときに再開できるよう残す。
    """
    from app.config import Config, JobsConfig

    root = _use_tmp_resume_dir(monkeypatch, tmp_path)
    monkeypatch.setattr("app.config.get_config",
                        lambda: Config(jobs=JobsConfig(auto_resume=False)))
    called = []

    def factory(params):
        called.append(params)

        async def run(job):
            return {}

        return run

    monkeypatch.setitem(jobs._resumable_kinds, "test.resume",
                        jobs._ResumableKind(factory, lambda params: "再開"))
    old_id = _seed_interrupted(root, tmp_path, kind="test.resume",
                               params={"alias": "m1"}, owner_user_id=None)

    assert _run(jobs.resume_after_startup()) == []
    assert called == []
    assert (root / f"{old_id}.json").exists()


def test_startup_discards_unregistered_broken_or_finished_records(client, monkeypatch, tmp_path):
    """目的: 再開できない記録（未登録の種類・壊れた JSON・中断以外で終わった Job）を
    起動後に消し、起動を止めないこと。

    根拠: 再開の対象を「登録済み・壊れていない・interrupted の行」に限る（m3-spec.md
    2026-11-26）。利用者が止めた Job を再起動で甦らせない。
    """
    root = _use_tmp_resume_dir(monkeypatch, tmp_path)
    unknown = _seed_interrupted(root, tmp_path, kind="test.unknown",
                                params={}, owner_user_id=None, job_id="resume-unknown")
    broken = _seed_interrupted(root, tmp_path, kind="test.resume",
                               params={}, owner_user_id=None, job_id="resume-broken")
    (root / f"{broken}.json").write_text("{not json", encoding="utf-8")
    canceled = _seed_interrupted(root, tmp_path, kind="test.resume",
                                 params={}, owner_user_id=None, job_id="resume-canceled",
                                 status="canceled")

    monkeypatch.setitem(jobs._resumable_kinds, "test.resume",
                        jobs._ResumableKind(lambda params: None, lambda params: "再開"))
    assert _run(jobs.resume_after_startup()) == []
    for job_id in (unknown, broken, canceled):
        assert not (root / f"{job_id}.json").exists()


def test_model_download_jobs_are_registered_as_resumable(client, monkeypatch):
    """目的: HF 取得と Strata 初回起動の Job が、自動再開の仕組みに載っていること。

    根拠: モデルのダウンロードを再起動後に続きから再開する（m3-spec.md 2026-11-26）。
    登録が外れると、再起動後の再開が黙って効かなくなる。
    """
    assert "model.hf_download" in jobs._resumable_kinds
    assert "model.strata.start" in jobs._resumable_kinds


def test_huggingface_download_runner_forwards_saved_params(monkeypatch):
    """目的: 保存した引数から作った HF 取得の runner が、同じ引数で hf.download を呼ぶこと。

    根拠: 再開の道が通常の取得と同じ処理を通る（m3-spec.md 2026-11-26）。通信はしない。
    2026-11-28: 大きさを Job の開始時に HF の API から読む目録の取得（fetch_sizes=true）が
    加わった。古い記録（fetch_sizes 無し）は従来どおり渡された値を使う。
    """
    from app.models_mgmt import hf, resume

    calls = {}

    async def fake_download(job, repo, files, **kwargs):
        calls.update({"repo": repo, "files": files, **kwargs})
        return {"ok": True}

    monkeypatch.setattr(hf, "download", fake_download)
    params = {
        "repo": "org/model-GGUF", "files": ["m-Q4_K_M.gguf"], "revision": "main",
        "library_id": "lib1", "expected_bytes": 123,
        "register": {"alias": "m", "role": None, "endpoint_id": None, "port": None},
    }
    runner = resume.huggingface_download_runner(params)
    result = _run(runner(jobs.Job(id="hf-resume", kind="model.hf_download", title="t")))
    assert result == {"ok": True}
    assert calls == {
        "repo": "org/model-GGUF", "files": ["m-Q4_K_M.gguf"], "revision": "main",
        "library_id": "lib1", "expected_bytes": 123, "fetch_sizes": False,
        "register": params["register"],
    }
    # 目録からの取得（fetch_sizes=true）: 再開でも「開始時に HF から大きさを読む」を引き継ぐ。
    calls.clear()
    runner = resume.huggingface_download_runner({**params, "fetch_sizes": True, "expected_bytes": 0})
    _run(runner(jobs.Job(id="hf-resume2", kind="model.hf_download", title="t")))
    assert calls["fetch_sizes"] is True and calls["expected_bytes"] == 0


def test_hf_download_endpoint_creates_resumable_job_without_secret(admin_client, monkeypatch, tmp_path):
    """目的: 画面から始めた HF 取得が再開可能な Job になり、 HF のトークンを記録しないこと。

    根拠: 取得の引数だけを残して再起動後に同じ処理を作り直す（m3-spec.md 2026-11-26）。
    画面の API と再開用 runner の接続が切れると、再開の登録だけ残って実際には動かない。
    """
    import json

    from app.models_mgmt import hf
    from tests.conftest import CSRF_HEADERS

    root = _use_tmp_resume_dir(monkeypatch, tmp_path)
    calls = {}

    async def fake_download(job, repo, files, **kwargs):
        calls.update({"repo": repo, "files": files, **kwargs})
        return {"ok": True}

    monkeypatch.setattr(hf, "download", fake_download)
    response = admin_client.post(
        "/api/v1/models/hf/download-jobs",
        json={"repo": "org/model-GGUF", "files": ["m-Q4.gguf"]},
        headers=CSRF_HEADERS,
    )
    assert response.status_code == 201, response.text
    job_id = response.json()["job_id"]
    doc = json.loads((root / f"{job_id}.json").read_text(encoding="utf-8"))
    assert doc["params"] == {
        "repo": "org/model-GGUF", "files": ["m-Q4.gguf"], "revision": "main",
        "library_id": "", "expected_bytes": 0, "register": None,
    }
    assert "token" not in json.dumps(doc)
    assert calls["repo"] == "org/model-GGUF"

    async def wait_file_gone():
        for _ in range(500):
            if not (root / f"{job_id}.json").exists():
                return
            await asyncio.sleep(0.01)

    _run(wait_file_gone())
    assert not (root / f"{job_id}.json").exists()
