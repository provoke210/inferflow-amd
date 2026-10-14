import json
import pytest
from inferflow.live_study import TokenGuard,StudyStore,workload,plan
from inferflow.storage import Store
from inferflow.upstream import UpstreamError


def test_live_plan_is_identical_across_arms_and_bounded():
    rows=workload('test')
    assert len(rows)==6
    assert len({r.cache_key() for _,r in rows if r.priority=='interactive'})==2
    assert all(r.max_tokens<=160 and r.timeout_seconds==90 for _,r in rows)
    assert plan('test')['workload_sha256']==plan('test')['workload_sha256']
    assert plan('test')['total_new_calls_cap']==30
    cold=plan('test',cold_only=True,calls_cap=15,token_cap=13466)
    assert cold['case_order']==['cold baseline','cold optimized']
    assert cold['calls_cap_this_invocation']==15 and cold['token_reservation_cap']==13466


def test_isolated_cache_keeps_shared_persistent_budget(tmp_path):
    ledger=Store(str(tmp_path/'ledger.db'))
    one,two=StudyStore(ledger),StudyStore(ledger)
    one.put('cache',{'value':1})
    one.attempt('completion')
    assert two.get('cache') is None
    assert one.budget_used()==two.budget_used()==1
    assert len(two.recent_attempt_times(60))==1
    one.close();two.close();ledger.close()


def test_token_guard_rejects_without_incrementing_actual_calls():
    guard=TokenGuard(cap=1400)
    payload=workload('test')[0][1].payload()
    guard.reserve(payload)
    prior=guard.actual_calls
    with pytest.raises(UpstreamError):
        guard.reserve(payload)
    assert guard.actual_calls==prior==1
    assert guard.reserved<=guard.cap
    with pytest.raises(UpstreamError):
        TokenGuard().reserve({**payload,'max_tokens':2048})
