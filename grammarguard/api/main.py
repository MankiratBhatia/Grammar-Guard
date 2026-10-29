"""FastAPI application factory.

python -m grammarguard.api            # serve on :8000 (docs at /docs)
"""

from __future__ import annotations

import json
import logging
import threading
import time
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import APIRouter, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from grammarguard.api import __version__
from grammarguard.api.errors import install_error_handlers
from grammarguard.api.routers import algebra, automata, benchmarks, generate, learn, system, validators
from grammarguard.api.services.compiler import CompileService
from grammarguard.api.settings import Settings
from grammarguard.decoding.engine import Engine, get_engine

log = logging.getLogger("grammarguard.api")

DESCRIPTION = """
Formal guarantees for LLM output, and formal QA for the validators behind them.

* **Automata**: regex -> ε-NFA -> DFA -> minimal DFA, membership with state paths.
* **Constraint algebra**: combine regular languages with ∩ ∪ − ¬ (product / complement constructions).
* **Validator Lab**: diff two validators with counterexamples; synthesize test suites.
* **Generate**: stream GPT-2 output constrained by a DFA or a JSON PDA, with per-token
  *constraint pressure* telemetry and verifiable certificates.
"""


def create_app(settings: Settings | None = None, engine: Engine | None = None) -> FastAPI:
    """Build the app. Tests pass their own settings and a fake engine."""
    settings = settings or Settings()
    engine = engine or get_engine(settings.model_name)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        if settings.warm_start:  # load the model in the background; /system/health reports when ready
            threading.Thread(target=engine.warm_up, daemon=True, name="warm-up").start()
        yield

    app = FastAPI(title="GrammarGuard", version=__version__, description=DESCRIPTION, lifespan=lifespan)
    app.state.settings = settings
    app.state.engine = engine
    app.state.compiler = CompileService(settings)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["X-Request-ID"],
    )

    @app.middleware("http")
    async def access_log(request: Request, call_next):
        """One JSON log line per request, and an X-Request-ID header to correlate with it."""
        request_id = request.headers.get("x-request-id") or uuid.uuid4().hex[:12]
        start = time.perf_counter()
        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        log.info(
            json.dumps(
                {
                    "id": request_id,
                    "method": request.method,
                    "path": request.url.path,
                    "status": response.status_code,
                    "ms": round((time.perf_counter() - start) * 1000, 1),
                }
            )
        )
        return response

    install_error_handlers(app)

    api = APIRouter(prefix="/api/v1")
    for module in (automata, algebra, validators, learn, generate, benchmarks, system):
        api.include_router(module.router)
    app.include_router(api)

    if settings.frontend_dir.exists():  # the built React app, served by the same process
        app.mount("/", StaticFiles(directory=settings.frontend_dir, html=True), name="frontend")
    else:

        @app.get("/", include_in_schema=False)
        def root() -> dict[str, str]:
            return {"name": "GrammarGuard", "version": __version__, "docs": "/docs"}

    return app


app = create_app()
