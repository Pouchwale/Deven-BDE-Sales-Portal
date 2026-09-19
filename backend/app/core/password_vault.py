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

Without PASSWORD_VIEW_KEY the key is derived from SECRET_KEY (see _cipher).
"""
from __future__ import annotations

import base64
import hashlib

from cryptography.fernet import Fernet, InvalidToken

from app.core.config import settings
from app.core.security import hash_password
from app.models.org import User


def _cipher() -> Fernet | None:
    """PASSWORD_VIEW_KEY when set. Otherwise a key derived from SECRET_KEY,
    so the reveal works on a host where nobody set the dedicated key - it is
    as secret as SECRET_KEY and as stable. Setting PASSWORD_VIEW_KEY is still
    better: it survives a SECRET_KEY rotation."""
    key = settings.PASSWORD_VIEW_KEY.strip()
    if key:
        return Fernet(key.encode("ascii"))
    secret = settings.SECRET_KEY.strip()
    if not secret:
        return None
    derived = hashlib.sha256(b"bde-portal-password-vault:" + secret.encode("utf-8")).digest()
    return Fernet(base64.urlsafe_b64encode(derived))


def refresh_copy(user: User, password: str) -> bool:
    """After a password was proven correct (a sign-in), make sure the stored
    copy can be read with the current key. Copies made under another key -
    e.g. copied from a different server - repair themselves this way.
    Returns True when it wrote."""
    if not enabled() or decrypt(user.password_encrypted) == password:
        return False
    user.password_encrypted = encrypt(password)
    return True


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
