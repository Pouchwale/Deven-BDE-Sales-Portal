"""FastAPI application entry point.

Startup never creates tables and never seeds: the schema comes from
`python -m app.db.migrate upgrade`, and accounts from the seed (development)
or `python -m app.seeds.bootstrap_admin` (production). The lifespan only
starts the SAP file watcher and, on a host that asks for it, the keepalive.

Run uvicorn with `--no-access-log`: RequestContextMiddleware writes the
access line itself, without the query string uvicorn's own line includes.
"""
from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

# Importing the models package registers every mapper before the first
# request configures a relationship.
import app.models  # noqa: F401
from app.api.router import api_router
from app.core.config import APP_VERSION, Settings, settings
from app.core.errors import register_error_handlers
from app.core.logging import configure_logging, get_logger
from app.core.middleware import REQUEST_ID_HEADER, RequestContextMiddleware
from app.services import keepalive, sap_sync, super_admin_env

configure_logging(settings.LOG_LEVEL, settings.log_format)
log = get_logger("app.startup")

CORS_METHODS = ["GET", "POST", "PATCH", "PUT", "DELETE", "OPTIONS"]
CORS_HEADERS = ["Content-Type", "X-CSRF-Token", "X-Request-ID", "Authorization"]


def fastapi_kwargs(config: Settings) -> dict[str, Any]:
    """Constructor arguments for the app. Separate so the production shape
    (no docs) can be tested without rebuilding the module-level app."""
    docs = config.docs_enabled
    return {
        "title": config.APP_NAME,
        "version": APP_VERSION,
        "description": (
            "BDE & Sales Portal - reference tracking, assigned leads and "
            "customer feedback, over a real reporting hierarchy."
        ),
        "docs_url": "/docs" if docs else None,
        "redoc_url": "/redoc" if docs else None,
        "openapi_url": "/openapi.json" if docs else None,
    }


def cors_kwargs(config: Settings) -> dict[str, Any]:
    origins = [o for o in config.cors_origins if o != "*"]
    regex = config.cors_origin_regex
    return {
        "allow_origins": origins,
        "allow_origin_regex": regex,
        # Credentials only ever alongside an explicit allow-list.
        "allow_credentials": bool(origins or regex),
        "allow_methods": CORS_METHODS,
        "allow_headers": CORS_HEADERS,
        "expose_headers": [REQUEST_ID_HEADER],
        "max_age": 600,
    }


def _log_startup_state(config: Settings) -> None:
    log.info(
        "starting",
        extra={"env": config.ENV, "version": APP_VERSION, "docs": config.docs_enabled},
    )
    if config.secret_key_generated:
        log.warning(
            "SECRET_KEY is not set; using a random per-process key. CSRF "
            "tokens will not survive a restart. Set SECRET_KEY in backend/.env."
        )
    if config.is_production:
        for problem in config.production_warnings():
            log.error("configuration: %s", problem)
    elif config.CHAT_ENABLED and not config.GROQ_API_KEY.strip():
        log.warning("CHAT_ENABLED is on but GROQ_API_KEY is blank; the assistant is disabled")


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    _log_startup_state(settings)
    # The Super Admin's username and password come from the env file.
    super_admin_env.sync_on_startup()
    # Keeps the portal in step with the live SAP workbook; a no-op unless
    # SAP_DATA_FILE is set and SAP_AUTO_SYNC is on.
    sap_sync.start()
    # Pings the portal's public URLs in working hours so Render's free tier
    # never idles it out; on by itself on Render only, never in local dev.
    keepalive.start()
    yield
    await keepalive.stop()
    sap_sync.stop()


app = FastAPI(lifespan=lifespan, **fastapi_kwargs(settings))

# Order matters: the last middleware added is the outermost. The request
# context layer wraps CORS so preflight responses get a request id and the
# security headers too.
app.add_middleware(CORSMiddleware, **cors_kwargs(settings))
app.add_middleware(
    RequestContextMiddleware,
    production=settings.is_production,
    hsts=settings.is_production and settings.COOKIE_SECURE,
    slow_request_ms=settings.SLOW_REQUEST_MS,
)

register_error_handlers(app)
app.include_router(api_router)


@app.get("/health", tags=["meta"])
def health() -> dict[str, str]:
    """Liveness probe. Deliberately does not touch the database, so it stays
    honest about the process rather than about the database.

    `env` is reported outside production only, so the end-to-end runners can
    refuse to drive a server that is not a disposable one (ENV=e2e). Every
    run creates leads and imports feedback the API cannot delete.
    """
    body = {"status": "ok", "app": settings.APP_NAME, "version": APP_VERSION}
    if not settings.is_production:
        body["env"] = settings.ENV
    return body


@app.get("/api/health/db", tags=["meta"], response_model=None)
def health_db() -> dict[str, str] | JSONResponse:
    """Readiness: can a connection run SELECT 1? Never returns the error."""
    from sqlalchemy import text

    from app.db.session import engine

    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
    except Exception as exc:  # noqa: BLE001 - reported as unavailable, logged here
        get_logger("app.health").error(
            "database health check failed: %s", type(exc).__name__
        )
        return JSONResponse({"status": "unavailable"}, status_code=503)
    body = {"status": "ok"}
    if not settings.is_production:
        body["dialect"] = engine.dialect.name
    return body
