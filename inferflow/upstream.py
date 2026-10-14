import asyncio
import hashlib
import time
from collections import deque
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import httpx


class UpstreamError(Exception):
    def __init__(self, status, retry_after=0, category="http"):
        self.status = status
        self.category = category
        self.retry_after = retry_after
        super().__init__(f"Upstream HTTP {status}")


def retry_seconds(value):
    try:
        return max(0, float(value))
    except (ValueError, TypeError):
        try:
            return max(0, (parsedate_to_datetime(value) - datetime.now(timezone.utc)).total_seconds())
        except (ValueError, TypeError, OverflowError):
            return 1.0


class AMDUpstream:
    simulated = False

    def __init__(self, settings):
        self.client = httpx.AsyncClient(base_url=settings.base_url.rstrip("/") + "/", headers={"Authorization": "Bearer " + settings.amd_api_key.get_secret_value()}, timeout=45, follow_redirects=False)

    async def request(self, method, path, payload=None):
        try:
            response = await self.client.request(method, path, json=payload)
        except httpx.HTTPError as exc:
            raise UpstreamError(502, category="transport:"+type(exc).__name__) from None
        if response.status_code >= 300:
            # Never propagate provider bodies, headers or credentials to logs/UI.
            raise UpstreamError(response.status_code, retry_seconds(response.headers.get("Retry-After")))
        try:
            return response.json()
        except ValueError:
            raise UpstreamError(502, category="invalid_json") from None

    async def complete(self, payload):
        return await self.request("POST", "chat/completions", payload)

    async def models(self):
        return await self.request("GET", "models")

    async def usage(self):
        return await self.request("GET", "usage?include_recent=true")

    async def close(self):
        await self.client.aclose()


class FakeUpstream:
    """Deterministic simulated latency/content, with real wall-clock limit enforcement."""
    simulated = True

    def __init__(self, rpm=120, concurrency=3, latency_scale=1, inject_429_every=0):
        self.rpm, self.concurrency = rpm, concurrency
        self.latency_scale = latency_scale
        self.inject_429_every = inject_429_every
        self.arrivals = deque()
        self.active = self.peak_active = self.calls = self.tokens = self.rejections = 0

    async def complete(self, payload):
        now = time.monotonic()
        while self.arrivals and self.arrivals[0] <= now-60:
            self.arrivals.popleft()
        self.calls += 1
        if (len(self.arrivals) >= self.rpm or self.active >= self.concurrency or
                (self.inject_429_every and self.calls % self.inject_429_every == 0)):
            self.rejections += 1
            raise UpstreamError(429, max(.05, self.arrivals[0]+60-now) if len(self.arrivals) >= self.rpm else .05)
        self.arrivals.append(now)
        self.active += 1
        self.peak_active = max(self.active, self.peak_active)
        try:
            content = str(payload["messages"][-1].get("content", ""))
            jitter = int(hashlib.sha256(content.encode()).hexdigest()[:4], 16) % 30 / 1000
            await asyncio.sleep(((.28 if payload["max_tokens"] > 128 else .08) + jitter) * self.latency_scale)
            if payload["max_tokens"] > 128:
                answer = "[SIMULATED SUMMARY] The customer reports a delivery or return issue. Ask a person to verify order and item details before applying policy. This is simulator text."
            elif "return" in content.lower():
                answer = "[SIMULATED DRAFT] Demo policy requires delivery within 7 days, an unused item and intact packaging. Confirm every condition through the return precheck and refer to a person. No automatic refund."
            else:
                answer = "[SIMULATED DRAFT] A person should verify delivery, return and product information. The simulator has no live inventory or tracking data."
            usage = {"prompt_tokens": max(1, sum(len(str(m.get("content", ""))) for m in payload["messages"])//3), "completion_tokens": len(answer)//3}
            usage["total_tokens"] = usage["prompt_tokens"] + usage["completion_tokens"]
            self.tokens += usage["total_tokens"]
            return {"id": "simulated", "model": payload["model"], "choices": [{"message": {"role": "assistant", "content": answer}, "finish_reason": "stop"}], "usage": usage, "simulated": True}
        finally:
            self.active -= 1

    async def models(self):
        return {"object": "list", "data": [{"id": "fake-commerce-v1", "object": "model"}], "simulated": True}

    async def usage(self):
        return {"simulated": True, "completion_attempts": self.calls, "estimated_tokens": self.tokens, "rejections": self.rejections}

    async def close(self):
        pass
