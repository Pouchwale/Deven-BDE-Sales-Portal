"""Password hashing and the CSRF token derivation.

bcrypt directly rather than passlib: passlib is unmaintained and its bcrypt
backend breaks against modern bcrypt releases.

Browser sessions are server-side rows (see app/core/sessions.py), not signed
tokens, so there is no JWT issuing here any more.
"""
from __future__ import annotations

import hashlib
import hmac
import secrets
import uuid
from functools import lru_cache

import bcrypt

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


@lru_cache(maxsize=1)
def _dummy_hash() -> str:
    # Random per process: it must never verify against anything a caller
    # could send.
    return bcrypt.hashpw(secrets.token_bytes(32).hex().encode(), bcrypt.gensalt()).decode()


def burn_password_check(password: str) -> None:
    """Spend the same bcrypt time as a real check, for an account that does
    not exist - so response timing does not reveal which emails are staff."""
    verify_password(password, _dummy_hash())


def csrf_token_for(session_id: uuid.UUID) -> str:
    """The CSRF value for one session.

    Derived from the session id with the server secret, so it cannot be
    chosen by an attacker (no fixation) and dies with the session.
    """
    message = f"csrf:{session_id}".encode()
    return hmac.new(settings.SECRET_KEY.encode("utf-8"), message, hashlib.sha256).hexdigest()


def constant_time_equals(a: str | None, b: str | None) -> bool:
    if not a or not b:
        return False
    return hmac.compare_digest(a.encode("utf-8"), b.encode("utf-8"))
