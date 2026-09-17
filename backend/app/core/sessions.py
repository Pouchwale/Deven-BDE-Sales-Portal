"""Server-managed sessions.

A session is a row in user_sessions. The client holds an opaque random secret
(in an HttpOnly cookie); the database holds only its SHA-256. Revoking the row
ends the session on the very next request - which a signed token on its own
cannot do.

Callers never commit here: every function works inside the caller's
transaction, so "reset the password" and "end their sessions" land together.
"""
from __future__ import annotations

import hashlib
import secrets
import uuid
from datetime import timedelta

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.base import as_naive_utc, utcnow
from app.models.org import User, UserSession


class RevokeReason:
    LOGOUT = "LOGOUT"
    PASSWORD_CHANGED = "PASSWORD_CHANGED"
    PASSWORD_RESET = "PASSWORD_RESET"
    DEACTIVATED = "DEACTIVATED"
    ADMIN = "ADMIN"
    ROLE_CHANGED = "ROLE_CHANGED"


def hash_token(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def create_session(
    db: Session,
    user: User,
    *,
    ip_address: str | None = None,
    user_agent: str | None = None,
) -> tuple[UserSession, str]:
    """Create a session and return (row, raw_secret). The raw secret is
    returned exactly once and must only ever travel in the session cookie."""
    raw = secrets.token_urlsafe(32)
    now = utcnow()
    row = UserSession(
        user_id=user.id,
        token_hash=hash_token(raw),
        created_at=now,
        last_seen_at=now,
        expires_at=now + timedelta(minutes=settings.SESSION_ABSOLUTE_TIMEOUT_MINUTES),
        ip_address=ip_address,
        user_agent=(user_agent or "")[:255] or None,
    )
    db.add(row)
    db.flush()
    return row, raw


def find_active_session(db: Session, raw: str) -> UserSession | None:
    """The live session for this secret, or None if unknown, revoked, expired
    or idle for longer than SESSION_IDLE_TIMEOUT_MINUTES."""
    if not raw:
        return None
    row = db.execute(
        select(UserSession).where(UserSession.token_hash == hash_token(raw))
    ).scalar_one_or_none()
    if row is None or row.revoked_at is not None:
        return None
    now = utcnow()
    if as_naive_utc(row.expires_at) <= now:
        return None
    idle = timedelta(minutes=settings.SESSION_IDLE_TIMEOUT_MINUTES)
    if as_naive_utc(row.last_seen_at) + idle <= now:
        return None
    return row


def touch(db: Session, row: UserSession) -> bool:
    """Slide the idle window. Written at most once a minute per session.
    Returns True when it wrote (so the caller knows there is something to
    commit)."""
    now = utcnow()
    if (now - as_naive_utc(row.last_seen_at)).total_seconds() >= 60:
        row.last_seen_at = now
        db.flush()
        return True
    return False


def revoke_session(db: Session, row: UserSession, reason: str) -> None:
    if row.revoked_at is None:
        row.revoked_at = utcnow()
        row.revoked_reason = reason
        db.flush()


def revoke_user_sessions(
    db: Session,
    user_id: uuid.UUID,
    reason: str,
    *,
    except_session_id: uuid.UUID | None = None,
) -> int:
    """End every live session a user holds. Returns how many were ended."""
    stmt = (
        update(UserSession)
        .where(UserSession.user_id == user_id, UserSession.revoked_at.is_(None))
        .values(revoked_at=utcnow(), revoked_reason=reason)
    )
    if except_session_id is not None:
        stmt = stmt.where(UserSession.id != except_session_id)
    result = db.execute(stmt.execution_options(synchronize_session=False))
    return int(result.rowcount or 0)
