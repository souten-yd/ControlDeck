from __future__ import annotations

import io
import json

import httpx
import pytest

from tests.conftest import CSRF_HEADERS
from tests.test_addon_api import addon_api
from tests.test_addon_contract import addon_manifest
from tests.test_addon_execution import _transport


@pytest.mark.parametrize('reference', ['/private/path', 'a\nb', 'https://example.com', 'x' * 129, 3, {}])
def test_upstream_failure_rejects_unbounded_or_nonopaque_references(reference: object) -> None:
    from app.addons import execution

    error = execution._upstream_error(httpx.Response(502, json={'detail': {
        'code': 'worker_failed', 'job_id': reference, 'status': 'failed',
        'message': '/private/worker/config', 'token': 'must-not-leak',
    }}))
    assert error.code == 'worker_failed'
    assert error.upstream_job_id is None and error.upstream_status is None
    assert '/private' not in str(error) and 'must-not-leak' not in str(error)


def test_mcp_failed_job_preserves_both_identifiers_and_safe_error_code(addon_api, monkeypatch) -> None:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import select
    from app.addons import execution
    from app.addons.agent_mcp import issue_opencode_token, router
    from app.database import SessionLocal
    from app.models import Job as JobRecord, User

    client, _registry = addon_api
    execution.reset_for_tests()
    assert client.post('/api/v1/addons', json=addon_manifest(), headers=CSRF_HEADERS).status_code == 201
    assert client.post('/api/v1/addons/fake-addon/enable', headers=CSRF_HEADERS).status_code == 200
    with SessionLocal() as db:
        user = db.execute(select(User).where(User.username == 'admin')).scalar_one()
        owner = user.id
    token = issue_opencode_token(owner, 'context-test')
    requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.startswith('/schemas/'):
            return httpx.Response(200, json={'type': 'object'})
        requests.append(request)
        return httpx.Response(502, json={'detail': {
            'code': 'worker_failed', 'job_id': 'opaque-job_123', 'status': 'failed',
            'message': '/private/worker/secret',
        }})

    monkeypatch.setattr(execution, '_client', _transport(handler))
    app = FastAPI()
    app.include_router(router, prefix='/api/v1')
    with TestClient(app) as caller:
        response = caller.post('/api/v1/addons/agent-mcp/call',
            headers={**CSRF_HEADERS, 'Authorization': 'Bearer ' + token},
            json={'name': 'fake.generate', 'arguments': {}})
    assert response.status_code == 502, response.text
    detail = response.json()['detail']
    assert detail['code'] == 'worker_failed'
    assert detail['upstream_job_id'] == 'opaque-job_123'
    assert detail['upstream_status'] == 'failed'
    assert detail['job_id'] != detail['upstream_job_id']
    assert len(requests) == 1 and '/private' not in response.text
    with SessionLocal() as db:
        job = db.get(JobRecord, detail['job_id'])
        assert job.status == 'failed' and job.owner_user_id == owner
        saved = json.loads(job.result_json)
        assert saved['error']['upstream_job_id'] == 'opaque-job_123'
        assert saved['error']['code'] == 'worker_failed'
        assert 'asset_id' not in saved


def test_stdio_error_keeps_references_even_with_a_long_explanation(monkeypatch) -> None:
    from app.integrations.opencode import addon_mcp_bridge as bridge

    body = {'detail': {'code': 'worker_failed', 'message': 'long ' * 500,
            'job_id': 'host-123', 'upstream_job_id': 'opaque-job_123', 'upstream_status': 'failed'}}
    def urlopen(request, timeout):
        raise bridge.urllib.error.HTTPError(request.full_url, 502, 'failed', {},
                                            io.BytesIO(json.dumps(body).encode()))
    monkeypatch.setenv('CONTROL_DECK_ADDON_MCP_URL', 'http://127.0.0.1:8765/api/v1/addons/agent-mcp')
    monkeypatch.setattr(bridge, '_token', 'test-only')
    monkeypatch.setattr(bridge.urllib.request, 'urlopen', urlopen)
    result = bridge.handle_message({'jsonrpc': '2.0', 'id': 1, 'method': 'tools/call',
                                   'params': {'name': 'fake.generate', 'arguments': {}}})['result']
    assert result['isError'] is True
    assert result['structuredContent'] == {k: v for k, v in body['detail'].items() if k != 'message'}
    text = result['content'][0]['text']
    assert 'host-123' in text and 'opaque-job_123' in text
    assert len(text) < 1100


def test_wait_timeout_retains_host_id_and_does_not_invent_upstream_id(monkeypatch) -> None:
    import asyncio
    from app.addons import execution
    from app.jobs import service as jobs

    class Job:
        id = 'host-timeout'
        status = 'running'
        task = None
        result = None
        error = None
        def __init__(self):
            self.changed = asyncio.Event()

    canceled = []
    async def cancel(job_id):
        canceled.append(job_id)
    monkeypatch.setattr(jobs, 'cancel_and_wait', cancel)
    async def run():
        with pytest.raises(execution.AddonExecutionError) as caught:
            await execution.wait_agent_tool_job(Job(), timeout=0.01)
        return caught.value
    error = asyncio.run(run())
    assert error.status_code == 504 and error.code == 'agent_tool_timeout'
    assert error.job_id == 'host-timeout' and error.upstream_job_id is None
    assert canceled == ['host-timeout']
