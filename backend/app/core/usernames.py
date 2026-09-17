"""Sign-in names ("navya", "superadmin"), defined once.

A username is an alias for the email address at sign-in, nothing more: it
grants nothing and appears in no permission check. Stored lowercase and
unique.
"""
from __future__ import annotations

import re
import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.constants import Role
from app.models.org import User

USERNAME_PATTERN = re.compile(r"^[a-z][a-z0-9._-]{1,39}$")
SUPER_ADMIN_USERNAME = "superadmin"


class InvalidUsername(ValueError):
    """Raised with wording safe to show a person."""


def normalise_login(identifier: str) -> str:
    """What was typed in the sign-in box, in the form it is stored.

    An email is lowercased. Anything else is a username: lowercased with every
    space removed, so "Super Admin" and "superadmin" are the same sign-in.
    """
    text = (identifier or "").strip().lower()
    if "@" in text:
        return text
    return "".join(text.split())


def validate_username(value: str) -> str:
    username = normalise_login(value)
    if "@" in username or not USERNAME_PATTERN.match(username):
        raise InvalidUsername(
            "Username must be 2-40 characters: letters, numbers, dots, dashes or "
            "underscores, starting with a letter."
        )
    return username


def is_taken(db: Session, username: str, *, exclude: uuid.UUID | None = None) -> bool:
    stmt = select(User.id).where(User.username == username)
    if exclude is not None:
        stmt = stmt.where(User.id != exclude)
    return db.execute(stmt).first() is not None


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (text or "").lower())


def _candidates(name: str, email: str, role: str) -> list[str]:
    words = [_slug(w) for w in (name or "").split()]
    words = [w for w in words if w]
    local = re.sub(r"[^a-z0-9._-]", "", (email or "").split("@", 1)[0].lower())

    options: list[str] = []
    if role == Role.SUPER_ADMIN:
        options.append(SUPER_ADMIN_USERNAME)
    if words:
        options.append(words[0])
    if local:
        options.append(local)
    if len(words) > 1:
        options.append(f"{words[0]}.{words[-1]}")
    return [o for o in options if USERNAME_PATTERN.match(o)]


def suggest_username(
    db: Session, *, name: str, email: str, role: str, exclude: uuid.UUID | None = None
) -> str:
    """The first free sign-in name for this person.

    First name, then the email's local part, then first.last, then the first
    name with a number - never a guess that collides with somebody else.
    """
    options = _candidates(name, email, role)
    for option in options:
        if not is_taken(db, option, exclude=exclude):
            return option
    base = options[0] if options else "user"
    n = 2
    while is_taken(db, f"{base}{n}", exclude=exclude):
        n += 1
    return f"{base}{n}"


def assign_missing_usernames(db: Session) -> list[tuple[str, str]]:
    """Give every account without a username one. Returns (name, username)
    pairs for what was assigned. Super Admins go first so the plain
    "superadmin" goes to one of them. Never commits."""
    users = db.execute(
        select(User).where(User.username.is_(None)).order_by(User.created_at, User.name)
    ).scalars().all()
    users = sorted(users, key=lambda u: u.role != Role.SUPER_ADMIN)
    assigned: list[tuple[str, str]] = []
    for user in users:
        user.username = suggest_username(
            db, name=user.name, email=user.email, role=user.role, exclude=user.id
        )
        db.flush()
        assigned.append((user.name, user.username))
    return assigned
