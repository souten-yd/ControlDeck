"""L2 の承認: 依頼・承認（その時点のコミットだけマージ）・却下と、Web の入口。"""
import subprocess

import pytest

from app.maintenance import approvals

SHA = "a" * 40


@pytest.fixture
def store(monkeypatch, tmp_path):
    monkeypatch.setattr(approvals, "data_dir", lambda: tmp_path)
    monkeypatch.setattr(approvals, "_gh", lambda: "/usr/bin/gh")
    return tmp_path


def _request(**patch):
    value = dict(repo="souten-yd/ControlDeck", pr=12, head_sha=SHA, title="権限の規則を変える",
                 summary="OpenCode から gh を使えるようにします。", risks=["AI に GitHub の権限を渡す"])
    value.update(patch)
    return approvals.create(**value)


def test_request_is_deduplicated_and_listed(store):
    first = _request()
    assert _request()["id"] == first["id"]
    assert [item["id"] for item in approvals.list_all()] == [first["id"]]
    assert (store / "approvals" / f"{first['id']}.json").stat().st_mode & 0o777 == 0o600


@pytest.mark.parametrize("patch", [{"repo": "bad repo"}, {"pr": 0}, {"head_sha": "abc"}, {"summary": " "}])
def test_bad_requests_are_rejected(store, patch):
    with pytest.raises(approvals.ApprovalError):
        _request(**patch)


def test_approve_merges_only_the_reviewed_commit(store, monkeypatch):
    calls = []

    def run(argv, **kwargs):
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr(approvals.subprocess, "run", run)
    record = approvals.approve(_request()["id"], user="admin")
    assert record["status"] == "merged" and record["decided_by"] == "admin"
    assert calls[0][-2:] == ["--match-head-commit", SHA]
    assert approvals.list_all() == []
    with pytest.raises(approvals.ApprovalError):
        approvals.approve(record["id"], user="admin")


def test_a_changed_pr_is_not_merged(store, monkeypatch):
    monkeypatch.setattr(approvals.subprocess, "run", lambda argv, **kw: subprocess.CompletedProcess(
        argv, 1, "", "GraphQL: Head branch was modified. Review and try the merge again. (mergePullRequest) head match"))
    record = approvals.approve(_request()["id"], user="admin")
    assert record["status"] == "failed" and "変わった" in record["result"]
    assert "GraphQL" not in record["result"], "gh の出力をそのまま見せない"


@pytest.fixture(autouse=True)
def _no_wait(monkeypatch):
    monkeypatch.setattr(approvals.time, "sleep", lambda _s: None)


def test_a_merge_github_reports_as_failed_but_did_land_counts_as_merged(store, monkeypatch):
    """gh can answer 'Merge already in progress' although the merge went through (seen on #411)."""
    import json as _json

    def run(argv, **kwargs):
        if argv[1:3] == ["pr", "merge"]:
            return subprocess.CompletedProcess(argv, 1, "", "GraphQL: Merge already in progress (mergePullRequest)")
        if argv[1:3] == ["pr", "view"] and "state,headRefOid" in argv:
            return subprocess.CompletedProcess(argv, 0, _json.dumps({"state": "MERGED", "headRefOid": SHA}), "")
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr(approvals.subprocess, "run", run)
    record = approvals.approve(_request(repo="souten-yd/PatentBuddy")["id"], user="admin")
    assert record["status"] == "merged"


def test_a_pr_merged_at_another_commit_is_still_a_failure(store, monkeypatch):
    import json as _json

    def run(argv, **kwargs):
        if argv[1:3] == ["pr", "merge"]:
            return subprocess.CompletedProcess(argv, 1, "", "error")
        return subprocess.CompletedProcess(argv, 0, _json.dumps({"state": "MERGED", "headRefOid": "c" * 40}), "")

    monkeypatch.setattr(approvals.subprocess, "run", run)
    assert approvals.approve(_request(repo="souten-yd/PatentBuddy")["id"], user="admin")["status"] == "failed"


def test_reject(store):
    record = approvals.reject(_request()["id"], user="admin", reason="今は不要")
    assert record["status"] == "rejected" and approvals.list_all() == []
    assert approvals.list_all(include_done=True)[0]["result"] == "今は不要"


def test_ids_cannot_escape(store):
    for bad in ("../x", "A" * 16, ""):
        with pytest.raises(approvals.ApprovalError):
            approvals.get(bad)


def test_web_requires_settings_manage(admin_client, monkeypatch, tmp_path):
    from fastapi.testclient import TestClient

    from app.main import app

    monkeypatch.setattr(approvals, "data_dir", lambda: tmp_path)
    record = _request()
    assert TestClient(app).get("/api/v1/system/approvals").status_code in (401, 403)
    listed = admin_client.get("/api/v1/system/approvals").json()["approvals"]
    assert [item["id"] for item in listed] == [record["id"]]
    headers = {"X-Requested-With": "ControlDeck"}
    assert admin_client.post(f"/api/v1/system/approvals/{record['id']}/delete", headers=headers).status_code == 404
    assert admin_client.post(f"/api/v1/system/approvals/{record['id']}/reject", json={},
                             headers=headers).json()["status"] == "rejected"


def test_an_approved_controldeck_pr_is_released_and_applied_promptly(store, monkeypatch):
    from app.maintenance import auto_update

    monkeypatch.setattr(auto_update, "data_dir", lambda: store)
    calls = []

    def run(argv, **kwargs):
        calls.append(argv[1:])
        if argv[1:3] == ["pr", "view"]:
            return subprocess.CompletedProcess(argv, 0, "b" * 40 + "\n", "")
        if argv[1:3] == ["release", "list"]:
            return subprocess.CompletedProcess(argv, 0, "ControlDeck_v0.16\nControlDeck_v0.9\nother\n", "")
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr(approvals.subprocess, "run", run)
    record = approvals.approve(_request()["id"], user="admin")
    assert record["status"] == "merged" and record["release_tag"] == "ControlDeck_v0.17"
    create = next(c for c in calls if c[:2] == ["release", "create"])
    assert create[2] == "ControlDeck_v0.17" and create[create.index("--target") + 1] == "b" * 40
    assert auto_update._prompt_requested("ControlDeck_v0.17")


def test_other_repositories_are_only_merged(store, monkeypatch):
    calls = []
    monkeypatch.setattr(approvals.subprocess, "run",
                        lambda argv, **kw: calls.append(argv[1:]) or subprocess.CompletedProcess(argv, 0, "", ""))
    record = approvals.approve(_request(repo="souten-yd/PatentBuddy")["id"], user="admin")
    assert record["status"] == "merged" and "release_tag" not in record
    assert not any(c[:2] == ["release", "create"] for c in calls)
