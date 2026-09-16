"""Declarative base and the two portable column types.

The models mirror app/db/sql/0001_initial.sql. They never create the schema -
the SQL migrations do that - so these types exist to read and write the
columns the SQL defined, identically on SQLite and PostgreSQL.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import CHAR, JSON, DateTime, TypeDecorator
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import DeclarativeBase


def utcnow() -> datetime:
    """UTC, naive.

    SQLite has no timestamptz, so the application invariant is "every stored
    timestamp is UTC" rather than "every timestamp carries its zone".
    Never call datetime.now() without a timezone anywhere in this codebase.
    """
    return datetime.now(timezone.utc).replace(tzinfo=None)


class GUID(TypeDecorator):
    """UUID stored as PostgreSQL `uuid`, or as CHAR(36) text elsewhere.

    SQLAlchemy's own Uuid type stores 32-char hex without dashes on SQLite,
    which does not match the CHAR(36) the migrations declare and makes hand
    queries against the dev database confusing. This keeps the canonical
    dashed form everywhere.
    """

    impl = CHAR(36)
    cache_ok = True

    def load_dialect_impl(self, dialect: Any) -> Any:
        if dialect.name == "postgresql":
            return dialect.type_descriptor(postgresql.UUID(as_uuid=True))
        return dialect.type_descriptor(CHAR(36))

    def process_bind_param(self, value: Any, dialect: Any) -> Any:
        if value is None:
            return None
        if not isinstance(value, uuid.UUID):
            value = uuid.UUID(str(value))
        return value if dialect.name == "postgresql" else str(value)

    def process_result_value(self, value: Any, dialect: Any) -> uuid.UUID | None:
        if value is None:
            return None
        return value if isinstance(value, uuid.UUID) else uuid.UUID(str(value))


# jsonb on PostgreSQL, TEXT-backed JSON on SQLite - matching {{JSON}}.
JSONType = JSON().with_variant(postgresql.JSONB(), "postgresql")

# Naive UTC on SQLite, timestamptz on PostgreSQL. The session forces the
# PostgreSQL connection to UTC so a naive value is never reinterpreted.
TimestampType = DateTime()


def new_uuid() -> uuid.UUID:
    """PKs are generated in Python so both dialects behave identically."""
    return uuid.uuid4()


class Base(DeclarativeBase):
    pass
