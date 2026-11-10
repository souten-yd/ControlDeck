from __future__ import annotations

import asyncio
from types import SimpleNamespace

import httpx
import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from app.addon_runtime import retrieval_ai as ai
from tests.test_addon_runtime_ai import runtime_ai_api
from tests.conftest import CSRF_HEADERS


def test_request_and_response_bounds():
    for body in [dict(capability='text.embed', inputs=['']),dict(capability='text.rerank',inputs=['x']),
                 dict(capability='text.embed', inputs=['x'],url='https://remote'),
                 dict(capability='text.embed', inputs=['x']*33),
                 dict(capability='text.embed', inputs=['語'*8192]*3)]:
        with pytest.raises(ValidationError):ai.RetrievalRequest(**body)
    good={'data':[{'index':1,'embedding':[0,1]},{'index':0,'embedding':[1,0]}]}
    assert ai.validate_response(good,'text.embed',2)['vectors']==[[1,0],[0,1]]
    for vector in ([float('nan')],[float('inf')],[True],[0,0]):
        with pytest.raises(ValueError):ai.validate_response({'data':[{'index':0,'embedding':vector}]},'text.embed',1)
    with pytest.raises(ValueError):ai.validate_response({'data':[{'index':0,'embedding':[1]},{'index':0,'embedding':[1]}]},'text.embed',2)
    with pytest.raises(ValueError):ai.validate_response({'results':[{'index':2,'relevance_score':1}]},'text.rerank',1)
    assert ai.validate_response({'results':[{'index':0,'relevance_score':0.1},{'index':1,'relevance_score':0.9}]},'text.rerank',2)['rankings'][0]['index']==1


def test_resolver_requires_local_role_and_opaque_revision(monkeypatch,tmp_path):
    from app.models_mgmt import llama
    model=tmp_path/'private-model.gguf';model.write_bytes(b'GGUF synthetic')
    item={'role':'embedding','alias':'private-alias','base_url':'http://127.0.0.1:1234/v1','model_path':str(model)}
    monkeypatch.setattr(llama,'list_instances',lambda:[item])
    first=ai.resolve_target('text.embed');assert len(first['model_ref'])==64
    model.write_bytes(b'GGUF changed synthetic');assert ai.resolve_target('text.embed')['model_ref']!=first['model_ref']
    for url in ('http://localhost:1234/v1','https://127.0.0.1:1234/v1','http://127.0.0.1:1234/v1?x=1','http://8.8.8.8:1234/v1'):
        item['base_url']=url
        with pytest.raises(ValueError):ai.resolve_target('text.embed')
    item['base_url']='http://127.0.0.1:1234/v1';item['role']='llm'
    with pytest.raises(ValueError):ai.resolve_target('text.embed')


def test_api_auth_bounds_and_no_identity_leak(runtime_ai_api,monkeypatch):
    client,headers,_=runtime_ai_api
    async def fake_execute(body,request):
        return {'vectors':[[1,0]],'dimensions':2,'model_ref':'a'*64,'execution_policy':'local_only','revision_kind':'host_file_metadata_v1'}
    monkeypatch.setattr(ai,'execute',fake_execute)
    path='/api/v1/addon-runtime/fake-addon/ai/retrieval/execute'
    result=client.post(path,headers=headers,json={'capability':'text.embed','inputs':['secret text']})
    assert result.status_code==200,result.text
    assert 'secret text' not in result.text and 'base_url' not in result.text
    assert client.post(path,headers=headers,json={'capability':'text.embed','inputs':['x'],'model':'injected'}).status_code==422
    assert client.post(path.replace('fake-addon','wrong-addon'),headers=headers,json={'capability':'text.embed','inputs':['x']}).status_code in (401,403)
    assert client.post('/api/v1/addons/fake-addon/disable',headers=CSRF_HEADERS,json={}).status_code==200
    assert client.post(path,headers=headers,json={'capability':'text.embed','inputs':['x']}).status_code==409


def test_lease_released_on_success_failure_and_cancellation(monkeypatch):
    async def scenario():
        from app.models_mgmt import gateway,llama
        target={'alias':'owned','base':'http://127.0.0.1:1','model_ref':'a'*64}
        monkeypatch.setattr(ai,'resolve_target',lambda *args:target)
        released=[]
        async def acquire(*args):return object(),'lease',object()
        async def release(*args):released.append(args[1])
        async def ready(*args,**kwargs):return True
        monkeypatch.setattr(gateway,'_acquire_gateway_lease',acquire)
        monkeypatch.setattr(gateway,'_release_gateway_lease',release)
        monkeypatch.setattr(llama,'ensure_ready',ready)
        body=ai.RetrievalRequest(capability='text.embed',inputs=['x'])
        async def good(*args):return {'vectors':[[1]],'dimensions':1}
        monkeypatch.setattr(ai,'fetch_result',good)
        assert (await ai.execute(body,SimpleNamespace()))['execution_policy']=='local_only'
        async def bad(*args):raise ValueError('bad')
        monkeypatch.setattr(ai,'fetch_result',bad)
        with pytest.raises(ValueError):await ai.execute(body,SimpleNamespace())
        started=asyncio.Event()
        async def pending(*args):started.set();await asyncio.Event().wait()
        monkeypatch.setattr(ai,'fetch_result',pending)
        task=asyncio.create_task(ai.execute(body,SimpleNamespace()));await started.wait();task.cancel()
        with pytest.raises(asyncio.CancelledError):await task
        assert released==['lease']*3
    asyncio.run(scenario())


def test_disconnect_cancels_work(monkeypatch):
    async def scenario():
        canceled=[]
        async def pending(*args):
            try:await asyncio.Event().wait()
            finally:canceled.append(True)
        async def disconnected():return True
        monkeypatch.setattr(ai,'execute',pending);monkeypatch.setattr(ai,'audit_runtime',lambda *args:None)
        with pytest.raises(HTTPException) as error:
            await ai.run(ai.RetrievalRequest(capability='text.embed',inputs=['x']),SimpleNamespace(is_disconnected=disconnected),None)
        assert error.value.status_code==499 and canceled==[True]
    asyncio.run(scenario())


def test_http_no_proxy_redirect_and_response_validation(monkeypatch):
    async def scenario():
        factory=httpx.AsyncClient;seen=[]
        def client(**kwargs):
            seen.append(kwargs)
            return factory(**kwargs,transport=httpx.MockTransport(lambda request:httpx.Response(302,headers={'Location':'https://cloud.invalid'})))
        monkeypatch.setattr(ai.httpx,'AsyncClient',client)
        with pytest.raises(httpx.HTTPStatusError):
            await ai.fetch_result({'base':'http://127.0.0.1:1','alias':'owned'},ai.RetrievalRequest(capability='text.embed',inputs=['x']))
        assert seen[0]['trust_env'] is False and seen[0]['follow_redirects'] is False
    asyncio.run(scenario())
