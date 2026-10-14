import asyncio
import time
import uuid
from collections import deque
from dataclasses import dataclass, field
from typing import Any
from .storage import summarize
from .upstream import UpstreamError


class GatewayError(Exception):
    def __init__(self, status, detail):
        self.status, self.detail = status, detail
        super().__init__(detail)


@dataclass(eq=False)
class Job:
    request: Any
    future: Any
    kind: str = "completion"
    id: str = field(default_factory=lambda: uuid.uuid4().hex)
    created: float = field(default_factory=time.monotonic)
    queued_at: float = field(default_factory=time.monotonic)
    queue_ms: float = 0
    attempts: int = 0
    rate_limited: int = 0
    upstream_tokens: int = 0
    task: Any = None
    done: bool = False

    @property
    def priority(self):
        return self.request.priority if self.request else "interactive"


class Engine:
    def __init__(self, upstream, store, rpm=120, concurrency=3, queue_size=64,
                 optimized=True, daily_budget=None, rate_window=60):
        self.upstream, self.store = upstream, store
        self.rpm, self.cap, self.limit = rpm, concurrency, concurrency
        self.queue_size, self.optimized = queue_size, optimized
        self.daily_budget, self.rate_window = daily_budget, rate_window
        self.queues = {"interactive": deque(), "background": deque()}
        self.jobs, self.active = {}, set()
        self.wake = asyncio.Event()
        self.next_send = self.cooldown = 0.0
        self.starts = deque(time.monotonic()-(time.time()-ts) for ts in store.recent_attempt_times(rate_window))
        if self.starts:
            self.next_send = self.starts[-1]+rate_window/rpm
        self.streak = self.recovery = 0
        self.runner = None
        self.closed = False
        self.started = time.monotonic()
        self.events = deque(maxlen=10000)

    async def start(self):
        self.runner = asyncio.create_task(self._dispatch())

    def _event(self, job, status, result=None, hit=False):
        if job.kind != "completion":
            return
        event = {"id": job.id, "priority": job.priority, "status": status,
            "queue_ms": round(job.queue_ms, 3), "e2e_ms": round((time.monotonic()-job.created)*1000, 3),
            "attempts": job.attempts, "rate_limited": job.rate_limited,
            "cache_hit": hit, "upstream_tokens": job.upstream_tokens,
            "served_tokens": (result or {}).get("usage", {}).get("total_tokens", 0)}
        self.store.record(event)
        self.events.append(event)

    def _finish(self, job, result=None, error=None, status="success", hit=False):
        if job.done:
            return
        job.done = True
        self.jobs.pop(job.id, None)
        self._event(job, status, result, hit)
        if not job.future.done():
            if error:
                job.future.set_exception(error)
            else:
                if job.kind == "completion":
                    result = {**result, "inferflow": {"request_id": job.id, "simulated": self.upstream.simulated,
                        "cache_hit": hit, "queue_ms": round(job.queue_ms, 3),
                        "e2e_ms": round((time.monotonic()-job.created)*1000, 3), "attempts": job.attempts}}
                job.future.set_result(result)
        self.wake.set()

    async def submit(self, request=None, kind="completion"):
        if self.closed:
            raise GatewayError(503, "Gateway is closing")
        job = Job(request, asyncio.get_running_loop().create_future(), kind=kind)
        if kind == "completion" and self.optimized:
            cached = self.store.get(request.cache_key())
            if cached:
                self._finish(job, result=cached, hit=True)
                return await job.future
        # Admission counts queued + active jobs, keeping retry queues bounded too.
        if sum(j.priority == job.priority for j in self.jobs.values()) >= self.queue_size:
            self._event(job, "queue_full")
            raise GatewayError(503, "Class capacity reached; retry later")
        self.jobs[job.id] = job
        self.queues[job.priority].append(job)
        self.wake.set()
        try:
            return await asyncio.wait_for(asyncio.shield(job.future), request.timeout_seconds if request else 30)
        except (asyncio.TimeoutError, asyncio.CancelledError) as exc:
            if not job.done:
                if job in self.queues[job.priority]:
                    self.queues[job.priority].remove(job)
                    job.queue_ms += (time.monotonic()-job.queued_at)*1000
                if job.task:
                    job.task.cancel()
                job.future.cancel()
                self._finish(job, status="timeout" if isinstance(exc, asyncio.TimeoutError) else "cancelled")
            if isinstance(exc, asyncio.TimeoutError):
                raise GatewayError(504, "End-to-end deadline exceeded") from None
            raise

    def _pick(self):
        iq, bq = self.queues["interactive"], self.queues["background"]
        if not self.optimized:
            pool = [q for q in (iq, bq) if q]
            return min(pool, key=lambda q: q[0].queued_at).popleft()
        # At most three interactive dispatches while a background job is waiting.
        if bq and (not iq or self.streak >= 3):
            self.streak = 0
            return bq.popleft()
        self.streak += 1
        return iq.popleft()

    async def _dispatch(self):
        while not self.closed:
            self.wake.clear()
            pending = any(self.queues.values())
            if pending and len(self.active) < self.limit:
                now = time.monotonic()
                while self.starts and self.starts[0] <= now-self.rate_window:
                    self.starts.popleft()
                ready = max(self.next_send, self.cooldown)
                if len(self.starts) >= self.rpm:
                    ready = max(ready, self.starts[0]+self.rate_window)
                delay = ready-now
                if delay > 0:
                    try:
                        await asyncio.wait_for(self.wake.wait(), delay)
                    except asyncio.TimeoutError:
                        pass
                    continue
                job = self._pick()
                job.queue_ms += (now-job.queued_at)*1000
                if job.done:
                    continue
                # Second lookup catches a previous exact request completed while this one queued.
                cached = self.store.get(job.request.cache_key()) if self.optimized and job.request else None
                if cached:
                    self._finish(job, result=cached, hit=True)
                    continue
                if self.daily_budget is not None and self.store.budget_used() >= self.daily_budget:
                    self._finish(job, error=GatewayError(429, "Local rolling 24h request budget exhausted"), status="budget_exhausted")
                    continue
                self.store.attempt(job.kind)
                self.starts.append(now)
                self.next_send = now + self.rate_window/self.rpm
                job.attempts += 1
                self.active.add(job)
                job.task = asyncio.create_task(self._execute(job))
                job.task.add_done_callback(lambda task, item=job: self._release(item))
                continue
            await self.wake.wait()

    def _release(self, job):
        self.active.discard(job)
        self.wake.set()

    async def _execute(self, job):
        try:
            if job.kind == "completion":
                result = await self.upstream.complete(job.request.payload())
                if not isinstance(result, dict) or not result.get("choices"):
                    raise UpstreamError(502)
                job.upstream_tokens += result.get("usage", {}).get("total_tokens", 0)
                if not job.done and self.optimized and result["choices"][0].get("finish_reason") == "stop" and not result["choices"][0].get("message", {}).get("tool_calls"):
                    self.store.put(job.request.cache_key(), result)
            else:
                result = await getattr(self.upstream, job.kind)()
            self.recovery += 1
            if self.optimized and self.recovery >= 6:
                self.limit = min(self.cap, self.limit+1)
                self.recovery = 0
            self._finish(job, result=result)
        except UpstreamError as exc:
            if exc.status == 429:
                job.rate_limited += 1
                self.recovery = 0
                if self.optimized:
                    self.limit = max(1, self.limit//2)
                self.cooldown = max(self.cooldown, time.monotonic()+max(exc.retry_after, .25*(2**(job.attempts-1))))
                if job.attempts < 3 and not job.done:
                    job.queued_at = time.monotonic()
                    self.queues[job.priority].append(job)
                    return
            self._finish(job, error=GatewayError(502 if exc.status != 429 else 429, f"Upstream HTTP {exc.status} ({exc.category})"), status=f"upstream_{exc.status}")
        except asyncio.CancelledError:
            if not job.done:
                job.future.cancel()
                self._finish(job, status="cancelled")
            raise
        except Exception:
            self._finish(job, error=GatewayError(502, "Invalid upstream response or internal processing failure"), status="internal_error")

    def snapshot(self):
        return {"simulated": self.upstream.simulated, "policy": "priority-adaptive-cache" if self.optimized else "fifo-fixed-no-cache",
                "queued": {k: len(v) for k, v in self.queues.items()}, "active": len(self.active),
                "concurrency": self.limit, "concurrency_cap": self.cap, "rpm": self.rpm,
                "cooldown_seconds": round(max(0, self.cooldown-time.monotonic()), 2),
                "local_budget_used_24h": self.store.budget_used(), "local_budget_cap": self.daily_budget,
                "metrics_scope": "retained completions since this process started (max 10000); all outcomes included",
                "metrics": summarize(list(self.events), time.monotonic()-self.started)}

    async def close(self):
        self.closed = True
        self.wake.set()
        if self.runner:
            await self.runner
        tasks = []
        for job in list(self.jobs.values()):
            if job.task and not job.task.done():
                job.task.cancel()
                tasks.append(job.task)
            job.future.cancel()
            self._finish(job, status="cancelled")
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        for q in self.queues.values():
            q.clear()
        await self.upstream.close()
