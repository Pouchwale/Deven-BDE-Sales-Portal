"""The password policy, defined once.

No length, character or common-word rules: the business chose to let people
pick any password. Two checks remain because they are not policy but facts:

* a password cannot be empty, and
* bcrypt reads at most 72 bytes, so anything longer is refused rather than
  silently cut short.

Every message is safe to show a person and says nothing about any stored
password.
"""
from __future__ import annotations

from app.core.security import MAX_PASSWORD_BYTES

#: Kept for callers that show the rule; any non-empty password is accepted.
MIN_PASSWORD_LENGTH = 1


class PasswordPolicyError(ValueError):
    """Raised with wording safe to show a person."""


def validate_password(password: str, *, email: str | None = None, name: str | None = None) -> str:
    # `email` and `name` are accepted so existing callers keep working; no rule
    # uses them any more.
    del email, name
    if not isinstance(password, str) or password == "":
        raise PasswordPolicyError("Enter a password.")
    if len(password.encode("utf-8")) > MAX_PASSWORD_BYTES:
        raise PasswordPolicyError(f"Password must be at most {MAX_PASSWORD_BYTES} bytes.")
    return password
