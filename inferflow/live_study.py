"""Explicit, bounded AMD study. Stop the gateway before running. No implicit execution.
Runs four small arms with a shared persistent request ledger and isolated caches.
"""
import argparse
import asyncio
import hashlib
import json
import time
from pathlib import Path
from .config import Settings
from .engine import Engine, GatewayError
from .schema import CompletionRequest
from .storage import Store, summarize
from .upstream import AMDUpstream, UpstreamError

POLICY = ('Support agent for a demo shop. Preserve stated facts and list only genuinely unknown facts. '
          'For delayed parcels, ask staff to check tracking; do not invent an ETA or suggest an unrequested refund. '
          'For wrong items, ask staff to verify the order and exchange stock; do not guarantee exchange. '
          'Returns require delivery within 7 days, unused item and intact packaging. '
          'If packaging is unknown, explicitly ask about it before claiming eligibility. '
          'A human makes the final decision. Do not invent fees. Keep answers under 55 words.')

TICKETS = [
    'Ticket A: parcel is two days late. Customer asks for an arrival date. No tracking update is available.',
    'Ticket B: customer received blue instead of ordered red. Delivery was yesterday. Item unused, packaging intact. Customer requests an exchange.',
    'Ticket C: delivered three days ago, unused item. Packaging state and return shipping fee unknown. Customer requests a return.'
]
FAQS = ['Delivered 3 days ago and unused. Can I return it?',
        'Delivered 10 days ago, unused, packaging intact. Am I eligible under this policy?']


def workload(model):
    def request(text, priority):
        return CompletionRequest(model=model, tenant='synthetic-study', knowledge_version='study-v2',
            priority=priority, temperature=0, top_p=1, max_tokens=160 if priority=='background' else 96,
            timeout_seconds=90, messages=[{'role':'system','content':POLICY},{'role':'user','content':text}])
    rows = [(i*.02, request('Summarize in exactly 3 labeled lines: Issue, Missing facts, Human next step. '+ticket, 'background')) for i,ticket in enumerate(TICKETS)]
    rows += [(.20,request(FAQS[0],'interactive')), (.40,request(FAQS[1],'interactive')), (.60,request(FAQS[0],'interactive'))]
    return rows


class StudyStore(Store):
    """Only cache/events are isolated. Real RPM and budget accounting is never reset."""
    def __init__(self, ledger):
        super().__init__()
        self.ledger = ledger
    def attempt(self, kind):
        self.ledger.attempt('live_study:'+kind)
    def recent_attempt_times(self, seconds):
        return self.ledger.recent_attempt_times(seconds)
    def budget_used(self):
        return self.ledger.budget_used()


class TokenGuard:
    def __init__(self, cap=24000):
        self.cap=cap
        self.reserved=0
        self.observed=0
        self.actual_calls=0
    def reserve(self, payload):
        # Conservative pre-send allowance, not an official tokenizer or billed-token guarantee.
        # UTF-8 JSON bytes plus protocol allowance and output cap. Only short text is allowed.
        amount=len(json.dumps(payload['messages'],ensure_ascii=False).encode())+256+payload['max_tokens']
        if payload['max_tokens']>160 or amount>1400 or self.reserved+amount>self.cap:
            raise UpstreamError(413)
        self.reserved+=amount
        self.actual_calls+=1


class GuardedAMD(AMDUpstream):
    def __init__(self, settings, guard):
        super().__init__(settings)
        self.guard=guard
    async def complete(self, payload):
        self.guard.reserve(payload)
        value=await super().complete(payload)
        self.guard.observed+=value.get('usage',{}).get('total_tokens',0)
        return value


def plan(model, cold_only=False, calls_cap=29, token_cap=24000):
    items=[{'arrival_seconds':t,'request':r.model_dump()} for t,r in workload(model)]
    return {'model':model,'workload':items,'workload_sha256':hashlib.sha256(json.dumps(items,sort_keys=True).encode()).hexdigest(),
        'case_order':['cold baseline','cold optimized'] if cold_only else ['cold baseline','cold optimized','warm optimized','warm baseline'],
        'calls_cap_after_preflight':29,'preflight_calls':1,'total_new_calls_cap':30,
        'budget_scope_note':'The 30-call ceiling describes the original session plan including one external preflight. This runner sends no preflight and enforces calls_cap_this_invocation against the persistent ledger.',
        'calls_cap_this_invocation':calls_cap,'cold_only':cold_only,
        'token_reservation_cap':token_cap,'max_output_tokens_per_request':160,
        'rpm':20,'concurrency':2,'provider_idle_seconds_before_measurement':61,
        'method':'Six identical scheduled requests per arm (3 summaries, 3 interactive including one exact repeat). Independent empty in-memory caches per arm. Both warm arms execute the same two unique FAQ warmups. The persistent RPM/budget ledger is shared and never cleared. Wait 61 seconds after the most recent real call before every measurement; this waits for the minute window naturally, without bypassing limits. Arms are sequential, with reversed order in the warm pair. No statistical inference from this one small sample. No TTFT or GPU/kernel claims.'}


async def run(settings, output, cold_only=False, calls_cap=29, token_cap=24000):
    if settings.mode!='amd':
        raise RuntimeError('Live study requires explicitly configured AMD mode')
    if settings.rpm>20 or settings.concurrency>2:
        raise RuntimeError('Study requires RPM <=20 and concurrency <=2')
    ledger=Store(settings.db_path)
    initial=ledger.budget_used()
    absolute_cap=min(settings.daily_budget,initial+calls_cap)
    if absolute_cap-initial<(13 if cold_only else 25):
        ledger.close()
        raise RuntimeError('Not enough local request budget for planned study; no requests sent')
    guard=TokenGuard(token_cap)
    report={**plan(settings.model or 'DeepSeek-V4-Flash',cold_only,calls_cap,token_cap),'simulated':False,'status':'running',
            'started_at_utc':time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime()),
            'initial_local_attempts_24h':initial,'absolute_request_cap':absolute_cap,'results':[],
            'cold_only':cold_only,'calls_cap_this_invocation':calls_cap,'token_reservation_cap':token_cap}
    if cold_only:
        report['case_order']=['cold baseline','cold optimized']
        report['scope_note']='Full cold pair repeated after a recorded HTTP 502 outage. Warm pair omitted to preserve the original overall call cap. Previous failed attempt remains in live-study-attempt1.json.'
    output.parent.mkdir(parents=True,exist_ok=True)
    def save():
        report['new_ledger_attempts_after_preflight']=ledger.budget_used()-initial
        report['generation_calls']=guard.actual_calls
        report['reserved_token_allowance']=guard.reserved
        report['observed_upstream_tokens']=guard.observed
        output.write_text(json.dumps(report,ensure_ascii=False,indent=2))
    async def idle_window():
        recent=ledger.recent_attempt_times(61)
        delay=max(0,recent[-1]+61-time.time()) if recent else 0
        if delay:
            print(f'Allowing the real provider minute window to expire: {delay:.1f}s',flush=True)
            while delay>0:
                await asyncio.sleep(min(delay,20))
                delay=max(0,recent[-1]+61-time.time())
    rows=workload(report['model'])
    save()
    try:
        for state,optimized in ([('cold',False),('cold',True)] if cold_only else [('cold',False),('cold',True),('warm',True),('warm',False)]):
            store=StudyStore(ledger)
            engine=Engine(GuardedAMD(settings,guard),store,rpm=settings.rpm,concurrency=settings.concurrency,
                          optimized=optimized,daily_budget=absolute_cap)
            await engine.start()
            case={'cache_state':state,'policy':'optimized' if optimized else 'baseline','warmup':{},'responses':[]}
            print(f'Starting {state} {case["policy"]}',flush=True)
            try:
                if state=='warm':
                    began=time.monotonic()
                    before=ledger.budget_used()
                    unique={r.cache_key():r for _,r in rows if r.priority=='interactive'}
                    warmed=await asyncio.gather(*(engine.submit(r) for r in unique.values()))
                    if any(not x.get('choices') or x['choices'][0].get('finish_reason')!='stop' for x in warmed):
                        raise RuntimeError('Warmup returned unusable or truncated output; study stopped')
                    case['warmup']={'requests':len(unique),'attempts':ledger.budget_used()-before,
                                    'tokens':sum(x.get('usage',{}).get('total_tokens',0) for x in warmed),
                                    'elapsed_seconds':round(time.monotonic()-began,3)}
                await idle_window()
                offset=len(store.events())
                began=time.monotonic()
                failed=asyncio.Event()
                async def send(index,arrival,request):
                    await asyncio.sleep(max(0,began+arrival-time.monotonic()))
                    try:
                        result=await engine.submit(request)
                        choice=result['choices'][0]
                        return {'index':index,'priority':request.priority,'arrival_seconds':arrival,
                                'content':choice.get('message',{}).get('content',''),
                                'finish_reason':choice.get('finish_reason'), 'usage':result.get('usage',{}),
                                'gateway':result['inferflow'], 'usable':bool(choice.get('message',{}).get('content')) and choice.get('finish_reason')=='stop'}
                    except asyncio.CancelledError:
                        return {"index":index,"priority":request.priority,"error":"cancelled after peer failure","usable":False}
                    except GatewayError as exc:
                        failed.set()
                        return {'index':index,'priority':request.priority,'arrival_seconds':arrival,
                                'error_status':exc.status,'error':exc.detail,'usable':False}
                tasks=[asyncio.create_task(send(i,t,r)) for i,(t,r) in enumerate(rows)]
                group=asyncio.gather(*tasks)
                fail_wait=asyncio.create_task(failed.wait())
                await asyncio.wait([group,fail_wait],return_when=asyncio.FIRST_COMPLETED)
                if failed.is_set():
                    for task in tasks:
                        if not task.done():
                            task.cancel()
                fail_wait.cancel()
                await asyncio.gather(fail_wait,return_exceptions=True)
                case['responses']=await group
                events=store.events()[offset:]
                case['events']=events
                case['metrics']=summarize(events,time.monotonic()-began)
                case['unusable_outputs']=sum(not x['usable'] for x in case['responses'])
                report['results'].append(case)
                save()
                print(json.dumps({'case':state+' '+case['policy'],'metrics':case['metrics'],'unusable':case['unusable_outputs']}),flush=True)
                if case['metrics']['errors'] or case['unusable_outputs']:
                    raise RuntimeError('Study stopped after errors/unusable outputs; partial results retained')
            finally:
                await engine.close();store.close()
        # Final quota observation consumes the same persistent request budget.
        store=StudyStore(ledger)
        engine=Engine(GuardedAMD(settings,guard),store,rpm=settings.rpm,concurrency=1,daily_budget=absolute_cap)
        await engine.start()
        try:
            usage=await engine.submit(kind='usage')
            report['quota_after']={k:v for k,v in usage.items() if k in {'rpm_limit','daily_cost_limit_usd','daily_cost_used_usd','daily_cost_remaining_usd','daily_reset_at'} and isinstance(v,(int,float,str))}
        finally:
            await engine.close();store.close()
        report['status']='complete'
    except BaseException as exc:
        report['status']='interrupted' if isinstance(exc,(asyncio.CancelledError,KeyboardInterrupt)) else 'failed'
        report['failure']=str(exc) if isinstance(exc,(RuntimeError,GatewayError)) else type(exc).__name__
        raise
    finally:
        save();ledger.close()
    return report


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--execute',action='store_true',help='Explicitly authorize the bounded live study')
    parser.add_argument('--output',default='results/live-study.json')
    parser.add_argument('--cold-only',action='store_true')
    parser.add_argument('--token-cap',type=int,default=24000,choices=range(1,24001))
    parser.add_argument('--calls-cap',type=int,default=29,choices=range(1,30))
    args=parser.parse_args()
    settings=Settings()
    if not args.execute:
        print(json.dumps(plan(settings.model or 'DeepSeek-V4-Flash',args.cold_only,args.calls_cap,args.token_cap),indent=2))
        return
    asyncio.run(run(settings,Path(args.output),args.cold_only,args.calls_cap,args.token_cap))

if __name__=='__main__':
    main()
