import asyncio
import time
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime
import pytest
from inferflow.engine import Engine, GatewayError
from inferflow.schema import CompletionRequest
from inferflow.storage import Store
from inferflow.upstream import FakeUpstream, UpstreamError, retry_seconds


def req(text='test', **kwargs):
    return CompletionRequest(model='fake-commerce-v1', messages=[{'role': 'user', 'content': text}], **kwargs)


async def make(**kwargs):
    upstream = kwargs.pop('upstream', FakeUpstream(rpm=100000, concurrency=10, latency_scale=.1))
    store = Store()
    engine = Engine(upstream, store, rpm=100000, concurrency=2, **kwargs)
    await engine.start()
    return engine, store, upstream


async def cleanup(engine, store):
    await engine.close()
    store.close()


async def test_cache_isolation_and_unsafe_requests():
    engine, store, upstream = await make()
    try:
        original = req('return')
        await engine.submit(original)
        assert (await engine.submit(original))['inferflow']['cache_hit']
        for changed in [req('return', tenant='other'), req('return', knowledge_version='v2'), req('return', max_tokens=100), req('return', seed=42), req('return', stop=['stop'])]:
            assert not (await engine.submit(changed))['inferflow']['cache_hit']
        assert upstream.calls == 6
        for unsafe in [req(temperature=.1), req(top_p=.9), req(tools=[{'type':'function'}]), req(cache=False), CompletionRequest(model='m', messages=[{'role':'tool','content':'x'}])]:
            assert unsafe.cache_key() is None
        assert req().cache_key() != req().model_copy(update={'model':'different'}).cache_key()
        assert req().cache_key() != req().model_copy(update={'messages':[{'role':'user','content':'test','name':'other'}]}).cache_key()
    finally:
        await cleanup(engine, store)


async def test_weighted_priority_and_fifo():
    for optimized, expected in [(True, ['i0','i1','i2','b0','i3','i4','b1']), (False, ['b0','b1','i0','i1','i2','i3','i4'])]:
        engine, store, _ = await make(optimized=optimized)
        engine.next_send = time.monotonic()+.06
        seen=[]
        original = engine.upstream.complete
        async def capture(payload):
            seen.append(payload['messages'][0]['content'])
            return await original(payload)
        engine.upstream.complete = capture
        try:
            tasks = [asyncio.create_task(engine.submit(req(f'b{i}', priority='background', cache=False))) for i in range(2)]
            tasks += [asyncio.create_task(engine.submit(req(f'i{i}', cache=False))) for i in range(5)]
            await asyncio.gather(*tasks)
            assert seen == expected
        finally:
            await cleanup(engine, store)


async def test_rpm_spacing_and_concurrency():
    upstream=FakeUpstream(rpm=100000, concurrency=2, latency_scale=.5)
    store=Store()
    engine=Engine(upstream,store,rpm=3,concurrency=2,rate_window=.15)
    await engine.start()
    starts=[]
    original=upstream.complete
    async def capture(payload):
        starts.append(time.monotonic())
        return await original(payload)
    upstream.complete=capture
    try:
        await asyncio.gather(*(engine.submit(req(str(i),cache=False)) for i in range(7)))
        assert all(b-a>=.045 for a,b in zip(starts,starts[1:]))
        assert all(starts[i+3]-starts[i]>=.145 for i in range(4))
        assert upstream.peak_active <= 2
    finally:
        await cleanup(engine,store)


async def test_429_cooldown_and_recovery():
    upstream=FakeUpstream(rpm=100000,concurrency=4,latency_scale=.1)
    store=Store();engine=Engine(upstream,store,rpm=100000,concurrency=4)
    original=upstream.complete
    timestamps=[]
    async def once(payload):
        timestamps.append(time.monotonic())
        if len(timestamps)==1:
            raise UpstreamError(429,.3)
        return await original(payload)
    upstream.complete=once
    await engine.start()
    try:
        await engine.submit(req('retry'))
        assert timestamps[1]-timestamps[0]>=.29
        assert engine.limit==2
        for i in range(5):
            await engine.submit(req(str(i)))
        assert engine.limit==3
        assert store.events()[0]['rate_limited']==1
        assert store.events()[0]['attempts']==2
    finally:
        await cleanup(engine,store)


async def test_deadline_pending_active_cancel_and_capacity():
    engine,store,upstream=await make(queue_size=1)
    engine.next_send=time.monotonic()+.2
    try:
        pending=asyncio.create_task(engine.submit(req(timeout_seconds=.05)))
        await asyncio.sleep(.01)
        with pytest.raises(GatewayError,match='capacity'):
            await engine.submit(req('second'))
        with pytest.raises(GatewayError) as exc:
            await pending
        assert exc.value.status==504
        assert not engine.jobs and upstream.calls==0
        engine.next_send=0
        upstream.latency_scale=10
        active=asyncio.create_task(engine.submit(req('active')))
        while upstream.active==0:
            await asyncio.sleep(.005)
        active.cancel()
        with pytest.raises(asyncio.CancelledError):
            await active
        await asyncio.sleep(.01)
        assert not engine.jobs and not engine.active and upstream.active==0
        assert store.get(req('active').cache_key()) is None
        assert {e['status'] for e in store.events()}=={'queue_full','timeout','cancelled'}
    finally:
        await cleanup(engine,store)


async def test_budget_survives_restart_and_metadata_counts(tmp_path):
    path=str(tmp_path/'budget.db')
    for run in range(2):
        store=Store(path)
        engine=Engine(FakeUpstream(),store,rpm=100000,concurrency=1,daily_budget=1)
        await engine.start()
        try:
            if run==0:
                await engine.submit(kind='models')
            with pytest.raises(GatewayError,match='budget'):
                await engine.submit(req())
            assert store.budget_used()==1
        finally:
            await cleanup(engine,store)


async def test_cache_ttl():
    store=Store(ttl=.01)
    store.put('a',{'value':1})
    assert store.get('a')
    await asyncio.sleep(.02)
    assert store.get('a') is None
    store.close()


def test_retry_after_numeric_and_date():
    assert retry_seconds('2')==2
    assert retry_seconds('nonsense')==1
    assert 1<retry_seconds(format_datetime(datetime.now(timezone.utc)+timedelta(seconds=3)))<=3


async def test_retry_exhaustion_keeps_errors_and_concurrency_bounded():
    engine,store,_=await make(upstream=FakeUpstream(rpm=100000,concurrency=2,inject_429_every=1))
    try:
        with pytest.raises(GatewayError) as exc:
            await engine.submit(req())
        assert exc.value.status==429
        event=store.events()[0]
        assert event['attempts']==event['rate_limited']==3
        assert event['status']=='upstream_429'
        assert engine.snapshot()['metrics']['errors']==1
        assert store.get(req().cache_key()) is None
    finally:
        await cleanup(engine,store)


async def test_shutdown_cancels_pending_and_active():
    engine,store,upstream=await make(upstream=FakeUpstream(rpm=100000,concurrency=2,latency_scale=20))
    tasks=[asyncio.create_task(engine.submit(req(str(i)))) for i in range(4)]
    await asyncio.sleep(.02)
    await engine.close()
    outcomes=await asyncio.gather(*tasks,return_exceptions=True)
    assert all(isinstance(x,asyncio.CancelledError) for x in outcomes)
    assert not engine.jobs and not engine.active and upstream.active==0
    assert len(store.events())==4
    store.close()


async def test_recent_rpm_is_preserved_across_restart(tmp_path):
    path=str(tmp_path/'rpm.db')
    store=Store(path)
    store.attempt('completion')
    store.close()
    store=Store(path)
    engine=Engine(FakeUpstream(latency_scale=.01),store,rpm=1,concurrency=1,rate_window=.15)
    await engine.start()
    start=time.monotonic()
    try:
        await engine.submit(req())
        assert time.monotonic()-start >= .13
    finally:
        await cleanup(engine,store)
