"""A recoverable copy of each password, for the Super Admin's "show password".

The business asked for the Super Admin to be able to read people's passwords.
Sign-in never uses this copy: it still checks only the bcrypt hash.

What keeps it from being a plaintext column:

* Encrypted with Fernet (AES + HMAC) under PASSWORD_VIEW_KEY, a key that
  lives only in the environment - never in the database, never in the repo.
  A database dump or backup on its own reveals nothing.
* Readable through exactly one endpoint, Super Admin only, one person at a
  time, and every read is written to the audit trail.
* Never part of any user listing, log line or audit payload.

Without PASSWORD_VIEW_KEY nothing is stored and nothing can be shown.
"""
from __future__ import annotations

from cryptography.fernet import Fernet, InvalidToken

from app.core.config import settings
from app.core.security import hash_password
from app.models.org import User


def _cipher() -> Fernet | None:
    key = settings.PASSWORD_VIEW_KEY.strip()
    if not key:
        return None
    return Fernet(key.encode("ascii"))


def enabled() -> bool:
    return _cipher() is not None


def encrypt(password: str) -> str | None:
    cipher = _cipher()
    return cipher.encrypt(password.encode("utf-8")).decode("ascii") if cipher else None


def decrypt(token: str | None) -> str | None:
    """The password, or None when there is no copy, no key, or the copy was
    made under a different key."""
    cipher = _cipher()
    if cipher is None or not token:
        return None
    try:
        return cipher.decrypt(token.encode("ascii")).decode("utf-8")
    except (InvalidToken, ValueError):
        return None


def set_password(user: User, password: str) -> None:
    """The one way a password is written: the bcrypt hash sign-in checks, and
    the encrypted copy the Super Admin may reveal."""
    user.hashed_password = hash_password(password)
    user.password_encrypted = encrypt(password)
