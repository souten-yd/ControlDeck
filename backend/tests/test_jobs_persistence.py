"""ジョブ基盤の DB 永続化・再起動復元・API のテスト。"""
import asyncio
import time


def _run(coro):
    return asyncio.run(coro)


def test_job_persisted_to_db(client):
    """ジョブ作成→完了が DB に記録され、get_any で再取得できる。"""
    from app.jobs import service as jobs

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
    from app.jobs import service as jobs

    jobs._jobs.pop(job_id, None)  # メモリから消す
    got = _run(jobs.get_any(job_id))
    assert got is not None
    assert got["status"] == "succeeded"
    assert got["result"]["answer"] == 42
    assert got.get("persisted") is True


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
        for _ in range(100):
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
