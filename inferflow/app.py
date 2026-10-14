import asyncio
import json
from contextlib import asynccontextmanager
from pathlib import Path
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.datastructures import Headers
from .benchmark import run_benchmark
from .config import Settings
from .engine import Engine, GatewayError
from .schema import CompletionRequest
from .returns import ReturnFacts, ReturnCheck, check_return
from .storage import Store
from .upstream import AMDUpstream, FakeUpstream


class LocalBoundary:
    """Pure ASGI middleware preserves disconnect messages for Request.is_disconnected."""
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http":
            headers = Headers(scope=scope)
            host = headers.get("host", "")
            origin = headers.get("origin")
            detail = None
            if headers.get("sec-fetch-site") == "cross-site" or (origin and origin != scope.get("scheme", "http")+"://"+host):
                detail = "Cross-origin access disabled"
            elif host.split(":")[0] not in {"127.0.0.1", "localhost", "testserver"}:
                detail = "Local host required"
            if detail:
                await JSONResponse({"detail": detail}, status_code=403)(scope, receive, send)
                return
        await self.app(scope, receive, send)


def create_app(settings=None):
    config = settings or Settings()

    @asynccontextmanager
    async def lifespan(app):
        store = Store(config.db_path, config.cache_ttl)
        upstream = AMDUpstream(config) if config.mode == "amd" else FakeUpstream(config.rpm, config.concurrency)
        engine = Engine(upstream, store, config.rpm, config.concurrency, config.queue_size, daily_budget=config.daily_budget if config.mode == "amd" else None)
        app.state.engine = engine
        app.state.models = []
        app.state.benchmark = None
        app.state.benchmark_task = None
        app.state.benchmark_error = None
        await engine.start()
        try:
            yield
        finally:
            if app.state.benchmark_task and not app.state.benchmark_task.done():
                app.state.benchmark_task.cancel()
                await asyncio.gather(app.state.benchmark_task, return_exceptions=True)
            await engine.close()
            store.close()

    app = FastAPI(title="InferFlow", lifespan=lifespan)
    app.mount("/assets", StaticFiles(directory=Path(__file__).parent / "static" / "assets"), name="assets")

    app.add_middleware(LocalBoundary)

    @app.exception_handler(GatewayError)
    async def gateway_error(request, exc):
        return JSONResponse({"detail": exc.detail}, status_code=exc.status)

    @app.get("/")
    async def index():
        return FileResponse(Path(__file__).parent / "static" / "index.html")

    @app.get("/api/status")
    async def status():
        return app.state.engine.snapshot()

    @app.get("/api/models")
    async def models():
        result = await app.state.engine.submit(kind="models")
        app.state.models = [x["id"] for x in result.get("data", []) if isinstance(x, dict) and "id" in x]
        return result

    @app.get("/api/usage")
    async def usage():
        return await app.state.engine.submit(kind="usage")

    @app.post("/api/returns/check", response_model=ReturnCheck)
    async def return_check(facts: ReturnFacts):
        # Independent of models, cache, quota, and generation. No business action.
        return check_return(facts)

    @app.post("/api/chat")
    async def chat(payload: CompletionRequest, request: Request):
        if payload.model not in app.state.models:
            raise HTTPException(400, "Load /api/models first and select an available model")
        task = asyncio.create_task(app.state.engine.submit(payload))
        try:
            while not task.done():
                if await request.is_disconnected():
                    task.cancel()
                    raise HTTPException(499, "Client disconnected")
                await asyncio.sleep(.02)
            return await task
        finally:
            if not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)

    @app.get("/api/live-study")
    async def live_study():
        path = Path(__file__).resolve().parent.parent / "results" / "live-study.json"
        if not path.exists():
            return {"available": False}
        report = json.loads(path.read_text())
        quality_path = path.with_name("live-study-quality.json")
        quality = json.loads(quality_path.read_text()) if quality_path.exists() else None
        return {"available": True, "report": report, "quality": quality}

    @app.post("/api/benchmark")
    async def benchmark():
        if app.state.benchmark_task and not app.state.benchmark_task.done():
            raise HTTPException(409, "Benchmark already running")
        app.state.benchmark = None
        app.state.benchmark_error = None
        async def run():
            try:
                app.state.benchmark = await run_benchmark()
            except Exception:
                app.state.benchmark_error = "Simulation failed; inspect local tests/environment"
        app.state.benchmark_task = asyncio.create_task(run())
        return {"state": "running", "simulated": True}

    @app.get("/api/benchmark")
    async def benchmark_status():
        return {"state": "running" if app.state.benchmark_task and not app.state.benchmark_task.done() else "idle",
                "simulated": True, "result": app.state.benchmark, "error": app.state.benchmark_error}

    return app


app = create_app()
