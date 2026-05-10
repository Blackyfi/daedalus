"""Daedalus FastAPI entrypoint.

Composes the auth, projects, tasks, ideas, connectors, runs and audit routers
behind a CSRF-resistant session cookie. mTLS is terminated by Caddy upstream,
which forwards the client cert fingerprint as `X-Client-Cert-Fingerprint`.
"""
from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse

from daedalus.api.routes import (
    audit,
    auth,
    connectors,
    diagnostics,
    discovery,
    ideas,
    internal,
    notes,
    notification_prefs,
    plans,
    projects,
    runs,
    system,
    tasks,
)

try:
    from daedalus.api.routes import webauthn  # requires the `webauthn` package
except ImportError:  # pragma: no cover - optional at import time
    webauthn = None  # type: ignore[assignment]
from daedalus.core.logging import configure_logging, log
from daedalus.core.settings import get_settings
from daedalus.db.base import get_engine
from daedalus.db.redis import close_redis, get_redis
from daedalus.storage.objects import get_object_store


@asynccontextmanager
async def lifespan(app: FastAPI):
    configure_logging()
    settings = get_settings()
    log.info("daedalus.boot", role=settings.role, public_url=settings.public_url)
    get_engine()
    redis = get_redis()
    await redis.ping()
    get_object_store().ensure_bucket()
    yield
    await close_redis()


app = FastAPI(
    title="Daedalus",
    version="0.1.0",
    docs_url="/api/docs",
    redoc_url=None,
    openapi_url="/api/openapi.json",
    lifespan=lifespan,
)


# Prometheus instrumentation — exposed at /metrics on the api container's
# 8000 port. Scraped from inside backnet by Prometheus; not exposed
# publicly through Caddy.
try:
    from prometheus_fastapi_instrumentator import Instrumentator

    Instrumentator().instrument(app).expose(app, endpoint="/metrics", include_in_schema=False)
except Exception:  # pragma: no cover - optional dep at runtime
    pass

app.add_middleware(
    CORSMiddleware,
    allow_origins=[get_settings().public_url],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

PREFIX = "/api/v1"
app.include_router(auth.router,       prefix=f"{PREFIX}/auth",      tags=["auth"])
if webauthn is not None:
    app.include_router(webauthn.router, prefix=f"{PREFIX}/auth/webauthn", tags=["auth"])
app.include_router(projects.router,   prefix=f"{PREFIX}/projects",  tags=["projects"])
app.include_router(tasks.router,      prefix=PREFIX,                tags=["tasks"])
app.include_router(ideas.router,      prefix=PREFIX,                tags=["ideas"])
app.include_router(notes.router,      prefix=PREFIX,                tags=["notes"])
app.include_router(connectors.router, prefix=f"{PREFIX}/connectors", tags=["connectors"])
app.include_router(plans.router,      prefix=PREFIX,                tags=["plans"])
app.include_router(runs.router,       prefix=f"{PREFIX}/runs",      tags=["runs"])
app.include_router(audit.router,      prefix=f"{PREFIX}/audit",     tags=["audit"])
app.include_router(discovery.router,  prefix=f"{PREFIX}/discover",  tags=["discovery"])
app.include_router(system.router,     prefix=f"{PREFIX}/system",    tags=["system"])
app.include_router(diagnostics.router, prefix=f"{PREFIX}/diagnostics", tags=["diagnostics"])
app.include_router(notification_prefs.router, prefix=f"{PREFIX}/account", tags=["account"])
app.include_router(internal.router,   prefix="/api/internal",       tags=["internal"])

STATIC_DIR = Path(__file__).resolve().parent / "static"


@app.get("/api/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/", include_in_schema=False)
async def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/app.js", include_in_schema=False)
async def app_js() -> FileResponse:
    return FileResponse(STATIC_DIR / "app.js", media_type="application/javascript")


@app.get("/styles.css", include_in_schema=False)
async def styles_css() -> FileResponse:
    return FileResponse(STATIC_DIR / "styles.css", media_type="text/css")
