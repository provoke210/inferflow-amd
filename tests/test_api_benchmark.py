import httpx
import pytest
from fastapi.testclient import TestClient
from inferflow.app import create_app
from inferflow.benchmark import workload, run_case
from inferflow.config import Settings
from inferflow.schema import CompletionRequest
from inferflow.storage import summarize
from inferflow.upstream import AMDUpstream, UpstreamError


def test_api_demo_and_boundary(tmp_path):
    app=create_app(Settings(db_path=str(tmp_path/'app.db'),rpm=100000))
    with TestClient(app) as client:
        assert client.get('/').status_code==200
        assert client.get('/api/models').json()['data'][0]['id']=='fake-commerce-v1'
        body={'model':'fake-commerce-v1','messages':[{'role':'user','content':'Can I return it?'}]}
        result=client.post('/api/chat',json=body)
        assert result.status_code==200
        assert result.json()['inferflow']['simulated']
        assert client.post('/api/chat',json=body).json()['inferflow']['cache_hit']
        assert client.get('/api/status').json()['metrics']['requests']==2
        assert client.get('/api/usage').json()['simulated']
        assert client.post('/api/chat',json={**body,'stream':True}).status_code==422
        assert client.post('/api/chat',json={**body,'model':'invented-model'}).status_code==400
        assert client.post('/api/chat',json=body,headers={'Origin':'https://evil.example'}).status_code==403
        assert client.get('/api/usage',headers={'Sec-Fetch-Site':'cross-site'}).status_code==403
        assert client.get('/api/status',headers={'Host':'evil.example'}).status_code==403


async def test_amd_adapter_paths_and_safe_errors():
    settings=Settings(mode='amd',amd_api_key='test-secret',limits_confirmed=True)
    adapter=AMDUpstream(settings)
    seen=[]
    async def handler(request):
        seen.append(str(request.url))
        assert request.headers['Authorization']=='Bearer test-secret'
        if request.url.path.endswith('/chat/completions'):
            return httpx.Response(429,headers={'Retry-After':'3'},text='test-secret provider body')
        return httpx.Response(200,json={'data':[{'id':'real-model'}]})
    await adapter.client.aclose()
    adapter.client=httpx.AsyncClient(base_url=settings.base_url+'/',headers={'Authorization':'Bearer test-secret'},transport=httpx.MockTransport(handler))
    try:
        await adapter.models();await adapter.usage()
        with pytest.raises(UpstreamError) as exc:
            await adapter.complete({})
        assert exc.value.retry_after==3
        assert 'test-secret' not in str(exc.value)
        assert seen==[settings.base_url+'/models',settings.base_url+'/usage?include_recent=true',settings.base_url+'/chat/completions']
    finally:
        await adapter.close()


async def test_benchmark_fairness_and_warmup():
    full=workload()
    assert [(t,r.model_dump()) for t,r in full]==[(t,r.model_dump()) for t,r in workload()]
    unique=CompletionRequest(model='fake-commerce-v1',messages=[{'role':'user','content':'return?'}])
    rows=[(0,unique),(.12,unique)]
    baseline=await run_case(rows,False,'warm',100000,3)
    optimized=await run_case(rows,True,'warm',100000,3)
    assert baseline['warmup']['requests']==optimized['warmup']['requests']==1
    assert baseline['warmup']['attempts']==optimized['warmup']['attempts']==1
    assert baseline['metrics']['requests']==optimized['metrics']['requests']==2
    assert baseline['metrics']['cache_hits']==0
    assert optimized['metrics']['cache_hits']==2
    assert baseline['metrics']['attempts']==2
    assert optimized['metrics']['attempts']==0


def test_errors_remain_in_denominator():
    template={'priority':'interactive','attempts':1,'rate_limited':0,'cache_hit':False,'upstream_tokens':0,'served_tokens':0,'queue_ms':1}
    summary=summarize([{**template,'status':'success','e2e_ms':10},{**template,'status':'timeout','e2e_ms':100}],2)
    assert summary['requests']==2 and summary['errors']==1
    assert summary['successful_rps']==.5
    assert summary['end_to_end']['p95_ms']==100
    assert summary['successful_end_to_end']['p95_ms']==10


def test_real_mode_requires_key_and_verified_limits():
    with pytest.raises(ValueError):
        Settings(mode='amd',amd_api_key='',limits_confirmed=False)
    with pytest.raises(ValueError):
        Settings(mode='amd',amd_api_key='secret',limits_confirmed=True,base_url='https://other.example')


def test_secrets_not_exposed_by_config_validation():
    with pytest.raises(ValueError) as exc:
        Settings(mode='amd',amd_api_key='never-print-this-key',limits_confirmed=False)
    assert 'never-print-this-key' not in str(exc.value)
    settings=Settings(mode='amd',amd_api_key='never-print-this-key',limits_confirmed=True)
    assert 'never-print-this-key' not in repr(settings)


async def test_real_http_disconnect_releases_upstream_slot(tmp_path):
    import asyncio
    import socket
    import uvicorn
    app=create_app(Settings(db_path=str(tmp_path/'disconnect.db'),rpm=100000))
    sock=socket.socket()
    sock.bind(('127.0.0.1',0))
    port=sock.getsockname()[1]
    server=uvicorn.Server(uvicorn.Config(app,log_level='critical'))
    server_task=asyncio.create_task(server.serve(sockets=[sock]))
    try:
        for _ in range(200):
            if server.started:
                break
            await asyncio.sleep(.01)
        assert server.started
        app.state.engine.upstream.latency_scale=20
        async with httpx.AsyncClient(base_url=f'http://127.0.0.1:{port}') as client:
            await client.get('/api/models')
            pending=asyncio.create_task(client.post('/api/chat',json={'model':'fake-commerce-v1','messages':[{'role':'user','content':'cancel this'}]}))
            for _ in range(100):
                if app.state.engine.upstream.active:
                    break
                await asyncio.sleep(.005)
            assert app.state.engine.upstream.active==1
            pending.cancel()
            await asyncio.gather(pending,return_exceptions=True)
            for _ in range(100):
                if not app.state.engine.jobs:
                    break
                await asyncio.sleep(.005)
            assert not app.state.engine.jobs
            assert app.state.engine.upstream.active==0
            assert app.state.engine.store.events()[-1]['status']=='cancelled'
    finally:
        server.should_exit=True
        await server_task
        sock.close()


def test_default_test_configuration_cannot_use_local_live_key():
    settings = Settings()
    assert settings.mode == 'fake'
    assert not settings.amd_api_key.get_secret_value()
