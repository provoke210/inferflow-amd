# Architecture

The local FastAPI app owns one asyncio Engine and one SQLite Store. Interactive and background submissions share outgoing pacing, concurrency, request accounting and deadlines. A pure ASGI boundary checks host/origin while preserving disconnect events. The HTTP route cancels the local task when the client disconnects.

The baseline dispatches FIFO at fixed concurrency with no cache. The optimized engine uses fair priority dispatch, exact caching and adaptive concurrency. Both share RPM, budget and retry semantics. Requests cannot preempt a call already in flight. Long Retry-After delays remain inside the request deadline. HTTP 502 is retained as failure rather than retried automatically in the experiment.

SQLite persists exact response caches, limited metric events, and outgoing attempt timestamps. Cache values may contain generated text. Events omit message bodies. This is single-process coordination, not production tenancy or distributed enforcement. The tenant field is a cache scope label, not an authenticated identity.

AMDUpstream implements model discovery, chat completions and usage. Error reporting omits provider bodies and secrets; HTTP failures and transport failures have distinct safe categories. FakeUpstream implements deterministic synthetic latency/content and actual wall-clock limit enforcement for tests and simulation.

The return precheck is independent of the generation engine. It accepts only explicit structured facts and produces fixed policy outcomes plus template explanations. No refund action follows. The static judge site runs a parity-tested JavaScript copy of this rule function, and serves curated saved API records and English materials. It has no provider key, no generation endpoint and no dependency on the local computer being online.

The public site illustrates the workflow and evidence. It is not a deployment of the Python live-inference service. Exposing the latter would require authentication, trusted facts, shared instance limits, persistent storage and retention controls.
