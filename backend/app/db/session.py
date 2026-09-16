"""Engine and session factory."""
from __future__ import annotations

from collections.abc import Iterator
from typing import Any

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import settings


def build_engine(url: str) -> Engine:
    kwargs: dict[str, Any] = {"pool_pre_ping": True, "future": True}

    if url.startswith("sqlite"):
        # FastAPI serves a request per thread; SQLite objects are otherwise
        # pinned to their creating thread.
        kwargs["connect_args"] = {"check_same_thread": False}
    else:
        # Timestamps are stored naive UTC (see app/db/base.utcnow). Pinning
        # the PostgreSQL session to UTC stops timestamptz reinterpreting a
        # naive value in the server's local zone.
        kwargs["connect_args"] = {"options": "-c timezone=UTC"}

    return create_engine(url, **kwargs)


engine = build_engine(settings.DATABASE_URL)


def _is_sqlite_connection(dbapi_connection: Any) -> bool:
    return type(dbapi_connection).__module__.startswith("sqlite3")


@event.listens_for(Engine, "connect")
def _sqlite_connect(dbapi_connection: Any, connection_record: Any) -> None:
    """Make SQLite behave like a real transactional database.

    Three things, none of them optional:

    * `foreign_keys=ON` - SQLite ships with foreign keys DISABLED, which
      silently turns every REFERENCES clause in the migrations into a
      comment.
    * `journal_mode=WAL` - readers do not block on a writer.
    * `isolation_level = None` - pysqlite's legacy transaction handling
      opens and commits transactions on its own schedule, which breaks
      SAVEPOINT and makes `ROLLBACK` unreliable. Turning it off hands
      transaction control to SQLAlchemy, which then emits BEGIN itself in
      the handler below. Without this pair, the promise that an audit row
      lands in the same transaction as the change it describes is not
      actually kept on SQLite.
    """
    if not _is_sqlite_connection(dbapi_connection):
        return
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.close()
    dbapi_connection.isolation_level = None


@event.listens_for(Engine, "begin")
def _sqlite_begin(conn: Any) -> None:
    """The other half of the pair above: SQLAlchemy now owns BEGIN."""
    if conn.engine.dialect.name == "sqlite":
        conn.exec_driver_sql("BEGIN")


SessionLocal = sessionmaker(
    bind=engine, autoflush=False, autocommit=False, expire_on_commit=False
)


def get_db() -> Iterator[Session]:
    """FastAPI dependency. One session per request, always closed."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
