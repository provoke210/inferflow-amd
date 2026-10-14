"""At most 3 initial upstream calls; retries disabled by using direct adapter.
Shares local budget ledger. Stop the gateway before running this script.
"""
import asyncio
import time
from inferflow.config import Settings
from inferflow.storage import Store
from inferflow.upstream import AMDUpstream, UpstreamError

async def main():
    settings = Settings()
    if settings.mode != 'amd':
        raise SystemExit('Configure local AMD mode first with scripts/configure_amd.py')
    store = Store(settings.db_path)
    upstream = AMDUpstream(settings)
    async def call(kind, fn):
        recent = store.recent_attempt_times(60)
        if recent:
            wait = max(0, recent[-1]+60/settings.rpm-time.time())
            if len(recent) >= settings.rpm:
                wait = max(wait, recent[-settings.rpm]+60-time.time())
            await asyncio.sleep(wait)
        if store.budget_used() >= settings.daily_budget:
            raise SystemExit('Local 24h budget exhausted')
        store.attempt(kind)
        value = await fn()
        await asyncio.sleep(60/settings.rpm)
        return value
    try:
        models = await call('models', upstream.models)
        ids = [x['id'] for x in models.get('data', [])]
        if not ids:
            raise SystemExit('No models returned; no completion sent')
        print('Available model IDs:', ', '.join(ids))
        await call('usage', upstream.usage)
        print('Usage endpoint: OK (account details not printed)')
        model = settings.model if settings.model in ids else ids[0]
        response = await call('completion', lambda: upstream.complete({'model': model, 'messages': [{'role': 'user', 'content': 'Reply only: InferFlow connected.'}], 'temperature': 0, 'max_tokens': 32}))
        if not response.get('choices'):
            raise SystemExit('Unexpected completion response shape')
        if not response['choices'][0].get('message', {}).get('content', '').strip():
            raise SystemExit('Completion returned no visible answer; real verification incomplete')
        print('LIVE AMD completion: OK; model:', model, '; tokens:', response.get('usage', {}).get('total_tokens', 'unknown'))
    except UpstreamError as exc:
        raise SystemExit(f'AMD returned HTTP {exc.status}; no retries performed')
    finally:
        await upstream.close()
        store.close()

asyncio.run(main())
