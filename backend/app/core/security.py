"""Password hashing and JWT issuing.

bcrypt directly rather than passlib: passlib is unmaintained and its bcrypt
backend breaks against modern bcrypt releases. PyJWT rather than python-jose
for the same reason.
"""
from __future__ import annotations

import secrets
import uuid
from datetime import datetime, timedelta, timezone

import bcrypt
import jwt

from app.core.config import settings

# bcrypt hashes at most 72 bytes and silently ignores the rest. Silently
# ignoring part of a password is unacceptable, so it is rejected instead.
MAX_PASSWORD_BYTES = 72


class PasswordTooLong(ValueError):
    pass


#: Deliberately no 0/O or 1/l/I. These passwords are read aloud or copied off
#: a screen, and a character somebody has to guess at is a support call.
_PASSWORD_ALPHABET = "abcdefghijkmnpqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789"


def generate_password(length: int = 16) -> str:
    """A strong password for an administrator to hand over.

    Exists because the portal CANNOT show an existing password - they are
    stored as one-way hashes, so the plaintext is not kept anywhere. The
    workable version of "let me see their password" is therefore "let me set
    one and read it once", and this produces the one being set.
    """
    return "".join(secrets.choice(_PASSWORD_ALPHABET) for _ in range(length))


def hash_password(password: str) -> str:
    encoded = password.encode("utf-8")
    if len(encoded) > MAX_PASSWORD_BYTES:
        raise PasswordTooLong(
            f"Password must be at most {MAX_PASSWORD_BYTES} bytes."
        )
    return bcrypt.hashpw(encoded, bcrypt.gensalt()).decode("utf-8")


def verify_password(password: str, hashed: str) -> bool:
    try:
        encoded = password.encode("utf-8")
        if len(encoded) > MAX_PASSWORD_BYTES:
            return False
        return bcrypt.checkpw(encoded, hashed.encode("utf-8"))
    except (ValueError, TypeError):
        # A malformed stored hash must fail closed, never raise a 500 that
        # tells an attacker the account exists.
        return False


def password_stamp(password_changed_at: datetime | None) -> str:
    """The value pinned into a token so a password change invalidates it.

    Comparing `iat` against password_changed_at cannot work: `iat` is whole
    seconds by convention, so a reset in the same second as the login would
    either fail to invalidate the old token or invalidate the new one. An
    exact stamp has no granularity to get wrong.
    """
    return password_changed_at.isoformat() if password_changed_at else ""


def create_access_token(
    user_id: uuid.UUID,
    *,
    password_changed_at: datetime | None = None,
    expires_minutes: int | None = None,
) -> str:
    now = datetime.now(timezone.utc)
    minutes = expires_minutes or settings.ACCESS_TOKEN_EXPIRE_MINUTES
    payload = {
        "sub": str(user_id),
        "pwd": password_stamp(password_changed_at),
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(minutes=minutes)).timestamp()),
    }
    return jwt.encode(payload, settings.SECRET_KEY, algorithm=settings.JWT_ALGORITHM)


def decode_access_token(token: str) -> dict | None:
    """Return the claims, or None for anything invalid or expired."""
    try:
        return jwt.decode(
            token, settings.SECRET_KEY, algorithms=[settings.JWT_ALGORITHM]
        )
    except jwt.PyJWTError:
        return None


def token_is_stale(claims: dict, password_changed_at: datetime | None) -> bool:
    """True when the password changed after this token was issued.

    A password change or an admin reset must invalidate every existing
    session immediately; without this a stolen token outlives the reset that
    was meant to kill it.
    """
    return claims.get("pwd", "") != password_stamp(password_changed_at)
