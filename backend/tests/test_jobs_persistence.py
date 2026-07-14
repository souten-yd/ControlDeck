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
