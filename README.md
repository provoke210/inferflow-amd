# InferFlow

Commerce inference scheduling and evaluation on **AMD Token Factory**.

- [Public judge demo](https://inferflow-amd-provoke210.spry-yak-0940.chatgpt.site)
- [Source repository](https://github.com/provoke210/inferflow-amd)
- [Real API study](docs/live-study.md) and [deterministic return policy](docs/return-policy.md)

The public site lets judges try a return-policy precheck, inspect unedited AMD responses, compare recorded results, and download English materials. It is a static evidence demo with browser-side rules. It does **not** expose the local gateway, a secret key, or a metered generation endpoint. The runnable Python gateway below provides real AMD inference when configured locally. Recorded evidence shows where AMD powered the product.

## Real workflow walkthrough

[Watch the English walkthrough](https://inferflow-amd-provoke210.spry-yak-0940.chatgpt.site/assets/materials/InferFlow-live-walkthrough-en.mp4) or inspect [the complete run record](results/live-walkthrough.json).

The local UI submitted three background summaries and then one customer question through AMD Token Factory, using DeepSeek-V4-Flash. All four generations succeeded, with no retry or cache hit. The customer response completed after the first summary and before the other two. This demonstration has no baseline and does not establish a speedup. The video uses real browser screenshots captured during the run, edited pacing and synthetic English narration; it is not an uninterrupted screen recording.

Quality review found unsupported advice to wait one more business day in the customer draft. The exact output is retained and needs human editing. The same walkthrough shows the independent structured check rejecting day 10, asking about unknown packaging and routing day 7 to human review without approving a refund.

This later run added 5 attempts (1 model discovery and 4 generations), bringing the local rolling 24-hour ledger from 35 to its existing 40-call ceiling. The earlier paired study's 27-of-30 accounting remains unchanged. No further AMD calls were made. Do not reset the ledger or raise the limit to continue testing.

## Run locally

Python 3.9+ is required. Run one worker only. Node is optional for the browser/Python parity test, not needed for the gateway.

```sh
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.lock
python -m pip install --no-deps -e .
python -m uvicorn inferflow.app:app --host 127.0.0.1 --port 8000 --workers 1
```

Open http://127.0.0.1:8000 on your own machine. The default provider is simulated. Opening the page makes no AMD calls. Use **Load available models** to enable drafts and batch summaries. API documentation is at `/docs`. `/?evidence=1` disables upstream controls while retaining local rule checks and recorded evidence.

## Verify without an AMD key

```sh
python -m pytest -q
python -m inferflow.benchmark --seed 1069 --output results/new-simulation.json
```

The benchmark always uses FakeUpstream, including when a local AMD key exists. Tests isolate `.env` and provider settings. With Node available, the suite also compares the entire browser-rule response to Python for all 45 combinations of boundary days and known/unknown conditions. Set `NODE_EXECUTABLE` to an absolute Node path if it is not on PATH.

## Configure real AMD inference

Read the RPM, concurrency and daily quota for the current **API key**. Limits are shared by programs using that key. The default 120 RPM / concurrency 3 is a simulator setting, not a provider entitlement.

Stop the gateway before credential setup or a standalone live experiment:

```sh
python scripts/configure_amd.py
python scripts/smoke_amd.py
python -m uvicorn inferflow.app:app --host 127.0.0.1 --port 8000 --workers 1
```

The configuration script uses hidden terminal input and creates `.env` with mode 0600. It refuses to overwrite a file and never prints the key. Never commit `.env`, databases, caches or credentials. Equivalent settings appear in `.env.example`. Real mode requires an API key and explicitly confirmed limits. The adapter permits only the configured approved AMD origin.

Live model discovery uses `GET /models`, generation uses `POST /chat/completions`, and the explicit usage button uses `GET /usage?include_recent=true`. The smoke script makes at most three calls: model discovery, usage and one short generation. The default local rolling 24-hour cap is 10 outgoing attempts, including metadata and retries. This is a request-count guard, not the provider's token or points quota.

The study used a locally configured 20 RPM, concurrency cap 2 and a temporary 40-request daily ceiling. Preserve `data/amd.sqlite3`: recent RPM timestamps and request accounting survive restart. Do not clear or rotate the ledger to evade limits. Cancellation cannot guarantee a provider refund or stop work already accepted upstream.

## What the gateway does

- Bounded interactive and background queues, 64 unfinished jobs per class. A waiting background job receives a scheduling opportunity after at most three interactive dispatches. Dispatch is non-preemptive.
- Shared pacing and a sliding RPM window across generation, metadata and retries. On 429, honor Retry-After, apply global backoff, halve concurrency, and recover by one after six successes up to the configured cap. At most three attempts. The baseline uses the same pacing, budget and retry rules with fixed concurrency, FIFO and no cache.
- Exact SHA-256 cache identity includes supported generation parameters, full messages, tenant and knowledge version. Only temperature 0, top_p 1, non-tool requests qualify. Only normal `stop` completions are stored. TTL is one hour, capacity 2,000. Temperature 0 is not a provider determinism guarantee; caching is an explicit reuse policy.
- Queue and end-to-end p50/p95, successful requests per second, 429/attempt rate, cache hits, errors and returned tokens. Failures remain in denominators. Unknown tokens from failed/cancelled calls are not treated as known zero cost. No streaming, TTFT, GPU kernel optimization or continuous batching claim.
- A deterministic return precheck for explicit delivery days, unused status and intact packaging. Unknown facts trigger questions. Passing conditions only routes to human review. No automatic return or refund is approved.

## Evidence and limits

The real cold pair had six requests per arm. Interactive median time changed from **13.946 s to 8.363 s**, while background median time increased from **5.826 s to 11.831 s**. One cache hit reduced generation attempts from six to five. Each class has only three requests; this is not a statistically significant production result. The first pair encountered HTTP 502 and remains archived. The live warm pair was omitted to stay within the 30-new-call ceiling; the study used 27.

Both real arms incorrectly approved a day-10 return under a 7-day policy. The new structured rule path prevents that decision in its workflow, but does not repair free-form model responses or prove the entered facts. Historical outputs and metrics remain unchanged. The English simulation is a new zero-quota run saved as `results/benchmark-en.json`; older multilingual synthetic fixtures are retained locally and excluded from this English release.

See [architecture](docs/architecture.md), [validation](docs/validation.md), [demo guide](docs/demo.md), [study](docs/live-study.md), [policy](docs/return-policy.md) and [event notes](docs/event-rules.md).

## Deployment boundary

The public static site can run while the developer's computer is off. Its source is generated from the English UI with `data-mode="public"`; model controls are hidden, no `/api` calls execute, and no credentials are deployed. A browser check is a demonstration, not an authoritative server transaction. The local API exposes the same rules server-side.

The local gateway intentionally binds to loopback and rejects cross-origin requests. It is a single-user prototype. Tenant labels are not authentication. Before exposing live inference, add authenticated access, verified order facts, shared limits across instances, retention controls and bounded usage. Do not simply bind this local gateway to a public interface.

## Docker

```sh
docker build -t inferflow .
docker run --rm -p 127.0.0.1:8000:8000 inferflow
```

For AMD mode, supply `--env-file .env` and persist `/app/data` in a writable volume. Keep a single instance. Docker configuration is included; no Docker executable was available for a build on the development machine.

MIT License. The repository and demo are published for review; publishing them does not submit the project to the competition.
