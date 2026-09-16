"""FastAPI application entry point."""
from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

# Importing the models package registers every mapper before the first
# request configures a relationship.
import app.models  # noqa: F401
from app.api.router import api_router
from app.core.config import settings
from app.core.errors import register_error_handlers
from app.services import sap_sync


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    # Keeps the portal in step with the live SAP workbook; a no-op unless
    # SAP_DATA_FILE is set and SAP_AUTO_SYNC is on.
    sap_sync.start()
    yield
    sap_sync.stop()


app = FastAPI(
    lifespan=lifespan,
    title=settings.APP_NAME,
    version="3.0.0",
    description=(
        "BDE & Sales Portal - reference tracking, assigned leads and "
        "customer feedback, over a real reporting hierarchy."
    ),
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_origin_regex=settings.cors_origin_regex,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

register_error_handlers(app)
app.include_router(api_router)


@app.get("/health", tags=["meta"])
def health() -> dict[str, str]:
    """Liveness probe. Deliberately does not touch the database, so it stays
    honest about the process rather than about the database.

    `env` is reported so the end-to-end runner can refuse to drive a server
    that is not a disposable one. Every run creates leads and imports
    feedback, and the API has no way to delete either - so pointed at the
    real database, it left 58 test leads and 16 test responses behind, and
    the dashboard reported them as the company's work.
    """
    return {
        "status": "ok",
        "app": settings.APP_NAME,
        "version": "3.0.0",
        "env": settings.ENV,
    }


@app.get("/api/health/db", tags=["meta"])
def health_db() -> dict[str, str]:
    from sqlalchemy import text

    from app.db.session import engine

    with engine.connect() as conn:
        conn.execute(text("SELECT 1"))
    return {"status": "ok", "dialect": engine.dialect.name}
