"""Fake-only reproducible paired benchmark. Never spends AMD quota."""
import argparse
import asyncio
import hashlib
import json
import random
import time
from pathlib import Path
from .engine import Engine, GatewayError
from .schema import CompletionRequest
from .storage import Store, summarize
from .upstream import FakeUpstream


def workload(seed=1069):
    rng = random.Random(seed)
    rows = []
    policy = {"role": "system", "content": "You are a demo commerce support agent. An unused item delivered within 7 days may be considered for a return. Refer unknown facts to a person and never promise a refund."}
    for i in range(12):
        rows.append((i*.015, CompletionRequest(model="fake-commerce-v1", messages=[policy, {"role": "user", "content": f"Summarize ticket {i}: a delayed parcel, with a customer asking about a return."}], priority="background", max_tokens=256, timeout_seconds=20)))
    for i in range(18):
        category = rng.randrange(4)
        rows.append((.2+i*.035, CompletionRequest(model="fake-commerce-v1", messages=[policy, {"role": "user", "content": f"Item {category} is unused. Can I return it?"}], timeout_seconds=20)))
    return sorted(rows, key=lambda row: row[0])


async def run_case(rows, optimized, state, rpm, concurrency):
    upstream = FakeUpstream(rpm=rpm, concurrency=concurrency)
    store = Store()
    engine = Engine(upstream, store, rpm=rpm, concurrency=concurrency, queue_size=128, optimized=optimized)
    await engine.start()
    warmup = {"requests": 0, "attempts": 0, "tokens": 0, "seconds": 0}
    try:
        if state == "warm":
            unique = {req.cache_key(): req for _, req in rows if req.priority == "interactive"}
            began = time.monotonic()
            # Equal warmup request set is executed for BOTH policies; cost is reported separately.
            await asyncio.gather(*(engine.submit(req) for req in unique.values()))
            warmup = {"requests": len(unique), "attempts": upstream.calls, "tokens": upstream.tokens, "seconds": round(time.monotonic()-began, 3)}
            # Match fresh provider window / limiter state for both measured phases.
            upstream.arrivals.clear()
            engine.starts.clear()
            engine.next_send = engine.cooldown = 0
            engine.limit = engine.cap
            engine.streak = engine.recovery = 0
        offset = len(store.events())
        began = time.monotonic()

        async def send(at, req):
            await asyncio.sleep(max(0, began+at-time.monotonic()))
            try:
                await engine.submit(req)
            except GatewayError:
                pass  # Errors remain in recorded denominator.

        await asyncio.gather(*(send(at, req) for at, req in rows))
        elapsed = time.monotonic()-began
        events = store.events()[offset:]
        return {"policy": "optimized" if optimized else "baseline", "cache_state": state,
                "metrics": summarize(events, elapsed), "warmup": warmup, "events": events,
                "fake_peak_concurrency": upstream.peak_active}
    finally:
        await engine.close()
        store.close()


async def run_benchmark(seed=1069, rpm=600, concurrency=3):
    rows = workload(seed)
    serialized = [{"arrival_seconds": at, "request": req.model_dump()} for at, req in rows]
    results = []
    for state in ("cold", "warm"):
        for optimized in (False, True):
            results.append(await run_case(rows, optimized, state, rpm, concurrency))
    return {"simulated": True, "seed": seed, "workload_sha256": hashlib.sha256(json.dumps(serialized, sort_keys=True).encode()).hexdigest(),
        "limits": {"rpm": rpm, "concurrency": concurrency}, "workload": serialized, "results": results,
        "method": "Same seeded arrivals/payloads/deadlines and limits for each policy. Separate fresh fake provider per case. Warm phase primes identical unique interactive FAQ requests for both policies; warmup costs reported separately, provider windows reset identically. Cold phase starts empty but may get within-run repeat hits. No streaming or TTFT. Wall-clock timings vary with host load. Not AMD/GPU measurements."}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", default="results/benchmark.json")
    parser.add_argument("--seed", type=int, default=1069)
    args = parser.parse_args()
    result = asyncio.run(run_benchmark(args.seed))
    path = Path(args.output)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(result, ensure_ascii=False, indent=2))
    print(f"SIMULATED benchmark saved to {path}")
    for row in result["results"]:
        m = row["metrics"]
        print(row["cache_state"], row["policy"], "interactive p95 ms:", m["by_priority"]["interactive"]["end_to_end"]["p95_ms"], "successful rps:", m["successful_rps"], "errors:", m["errors"])


if __name__ == "__main__":
    main()
