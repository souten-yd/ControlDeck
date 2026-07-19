import json
import time

from tests.conftest import CSRF_HEADERS


def _definition(value: str = "{{start.question}}") -> dict:
    return {
        "nodes": [
            {
                "id": "start", "type": "trigger", "name": "秘密の内部トリガー",
                "config": {"mode": "manual", "inputs": [{
                    "key": "question", "label": "質問", "type": "paragraph",
                    "required": True, "placeholder": "入力してください", "sample": "Ubuntuとは？",
                }]},
            },
            {
                "id": "private-output-node", "type": "output.render", "name": "内部出力ノード",
                "config": {
                    "name": "answer", "title": "回答", "renderer": "markdown",
                    "description": "公開結果", "value": value,
                },
            },
        ],
        "edges": [{"source": "start", "target": "private-output-node"}],
    }


def test_runner_exposes_only_published_contract_and_runs_immutable_version(admin_client):
    created = admin_client.post(
        "/api/v1/workflows",
        json={"name": "公開Q&A", "description": "質問に回答します", "definition": _definition()},
        headers=CSRF_HEADERS,
    )
    assert created.status_code == 201, created.text
    workflow_id = created.json()["id"]
    published = admin_client.post(f"/api/v1/workflows/{workflow_id}/publish", headers=CSRF_HEADERS)
    assert published.status_code == 200, published.text

    listing = admin_client.get("/api/v1/workflow-runner")
    assert listing.status_code == 200
    summary = next(item for item in listing.json() if item["id"] == workflow_id)
    assert summary["name"] == "公開Q&A"
    assert summary["input_count"] == 1 and summary["output_count"] == 1

    detail_response = admin_client.get(f"/api/v1/workflow-runner/{workflow_id}")
    assert detail_response.status_code == 200
    detail = detail_response.json()
    field = detail["input_schema"]["x-control-deck-fields"][0]
    assert field["key"] == "question" and field["sample"] == "Ubuntuとは？"
    assert detail["output_schema"]["x-control-deck-outputs"][0]["name"] == "answer"
    serialized = json.dumps(detail, ensure_ascii=False)
    for forbidden in ("definition", "nodes", "edges", "private-output-node", "秘密の内部トリガー", "config", "runtime_snapshot"):
        assert forbidden not in serialized

    # draftを書き換えてもRunnerは再公開まで公開版を実行する。
    changed = admin_client.patch(
        f"/api/v1/workflows/{workflow_id}",
        json={"description": "DRAFT DESCRIPTION", "definition": _definition("DRAFT-ONLY")},
        headers=CSRF_HEADERS,
    )
    assert changed.status_code == 200
    assert admin_client.get(f"/api/v1/workflow-runner/{workflow_id}").json()["description"] == "質問に回答します"

    missing = admin_client.post(
        f"/api/v1/workflow-runner/{workflow_id}/runs", json={"input": {}}, headers=CSRF_HEADERS,
    )
    assert missing.status_code == 422
    unknown = admin_client.post(
        f"/api/v1/workflow-runner/{workflow_id}/runs",
        json={"input": {"question": "hello", "private": "no"}}, headers=CSRF_HEADERS,
    )
    assert unknown.status_code == 422

    started = admin_client.post(
        f"/api/v1/workflow-runner/{workflow_id}/runs",
        json={"input": {"question": "公開入力"}}, headers=CSRF_HEADERS,
    )
    assert started.status_code == 200, started.text
    execution_id = started.json()["execution_id"]
    for _ in range(60):
        run = admin_client.get(f"/api/v1/workflow-runner/executions/{execution_id}")
        assert run.status_code == 200
        if run.json()["status"] not in ("QUEUED", "RUNNING", "WAITING"):
            break
        time.sleep(0.05)
    body = run.json()
    assert body["status"] == "SUCCEEDED", body
    assert body["input"] == {"question": "公開入力"}
    assert body["outputs"]["answer"]["value"] == "公開入力"
    assert "source_node_id" not in body["outputs"]["answer"]
    serialized = json.dumps(body, ensure_ascii=False)
    for forbidden in ("definition_snapshot", "runtime_snapshot", "context", "private-output-node", "DRAFT-ONLY"):
        assert forbidden not in serialized

    republished = admin_client.post(f"/api/v1/workflows/{workflow_id}/publish", headers=CSRF_HEADERS)
    assert republished.status_code == 200
    assert republished.json()["version"] > published.json()["version"]
    current = admin_client.get(f"/api/v1/workflow-runner/{workflow_id}").json()
    assert current["description"] == "DRAFT DESCRIPTION"


def test_runner_rejects_unpublished_workflow(admin_client):
    created = admin_client.post(
        "/api/v1/workflows", json={"name": "下書きのみ", "definition": _definition()}, headers=CSRF_HEADERS,
    )
    assert created.status_code == 201
    workflow_id = created.json()["id"]
    assert admin_client.get(f"/api/v1/workflow-runner/{workflow_id}").status_code == 404
    assert admin_client.post(
        f"/api/v1/workflow-runner/{workflow_id}/runs", json={"input": {"question": "x"}}, headers=CSRF_HEADERS,
    ).status_code == 404


def test_run_only_operator_uses_runner_without_definition_access(admin_client):
    from sqlalchemy import select

    from app.database import SessionLocal
    from app.models import Role, User
    from app.security.passwords import hash_password

    created = admin_client.post(
        "/api/v1/workflows", json={"name": "Operator公開", "definition": _definition()}, headers=CSRF_HEADERS,
    )
    workflow_id = created.json()["id"]
    assert admin_client.post(f"/api/v1/workflows/{workflow_id}/publish", headers=CSRF_HEADERS).status_code == 200
    started = admin_client.post(
        f"/api/v1/workflow-runner/{workflow_id}/runs",
        json={"input": {"question": "operator"}}, headers=CSRF_HEADERS,
    )
    execution_id = started.json()["execution_id"]

    with SessionLocal() as db:
        role = db.execute(select(Role).where(Role.name == "operator")).scalar_one()
        user = db.execute(select(User).where(User.username == "runner_operator")).scalar_one_or_none()
        if user is None:
            db.add(User(
                username="runner_operator", display_name="Runner Operator",
                password_hash=hash_password("runner-password-123"), role_id=role.id,
            ))
            db.commit()
    admin_client.cookies.clear()
    login = admin_client.post(
        "/api/v1/auth/login", json={"username": "runner_operator", "password": "runner-password-123"},
        headers=CSRF_HEADERS,
    )
    assert login.status_code == 200
    assert admin_client.get("/api/v1/workflows").status_code == 403
    assert admin_client.get(f"/api/v1/workflows/{workflow_id}").status_code == 403
    assert admin_client.get(f"/api/v1/workflow-executions/{execution_id}").status_code == 403
    assert admin_client.get("/api/v1/workflow-runner").status_code == 200
    detail = admin_client.get(f"/api/v1/workflow-runner/{workflow_id}")
    assert detail.status_code == 200
    assert "definition" not in detail.text and "private-output-node" not in detail.text
