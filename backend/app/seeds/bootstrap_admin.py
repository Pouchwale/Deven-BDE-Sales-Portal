"""Create the first Super Admin - once.

    python -m app.seeds.bootstrap_admin --email owner@example.com --name "Portal Owner"

The production way to get the first account: the roster seed never creates
users there. It refuses (exit code 1) when an ACTIVE Super Admin already
exists, so it cannot be used to mint a second back door later.

The password is never taken from the command line (argv is visible to every
process on the machine and lands in shell history). It is read with getpass,
twice, or - for unattended provisioning - from the BOOTSTRAP_ADMIN_PASSWORD
environment variable. It must pass the portal's password policy. Nothing
secret is printed.

Flags
    --must-change-password   force a password change at first sign-in
"""
from __future__ import annotations

import argparse
import getpass
import os
import sys

from email_validator import EmailNotValidError, validate_email
from sqlalchemy import func, select
from sqlalchemy.orm import Session

import app.models  # noqa: F401  (registers every mapper)
from app.core.constants import AuditAction, EntityType, Role
from app.core.passwords import PasswordPolicyError, validate_password
from app.core import password_vault
from app.core.usernames import suggest_username
from app.models.org import User
from app.services import audit

PASSWORD_ENV_VAR = "BOOTSTRAP_ADMIN_PASSWORD"


class BootstrapRefused(RuntimeError):
    """Safe to print: never contains the password."""


def active_super_admin_exists(db: Session) -> bool:
    count = db.scalar(
        select(func.count(User.id)).where(
            User.role == str(Role.SUPER_ADMIN), User.is_active.is_(True)
        )
    )
    return bool(count)


def bootstrap(
    db: Session,
    *,
    email: str,
    name: str,
    password: str,
    must_change_password: bool = False,
) -> User:
    """Create the Super Admin in `db` (flushed, not committed)."""
    if active_super_admin_exists(db):
        raise BootstrapRefused(
            "An active Super Admin already exists. Bootstrap is for a brand-new "
            "installation only; create further accounts in the portal."
        )

    name = " ".join((name or "").split())
    if not name:
        raise BootstrapRefused("--name is required.")
    try:
        email = validate_email(email, check_deliverability=False).normalized.lower()
    except EmailNotValidError as exc:
        raise BootstrapRefused(f"--email is not a valid address: {exc}") from None

    existing = db.execute(
        select(User).where(func.lower(User.email) == email)
    ).scalars().first()
    if existing is not None:
        raise BootstrapRefused(
            "An account with that email already exists. Bootstrap does not "
            "promote or reactivate existing accounts."
        )

    try:
        validate_password(password, email=email, name=name)
    except PasswordPolicyError as exc:
        raise BootstrapRefused(str(exc)) from None

    user = User(
        name=name,
        email=email,
        username=suggest_username(db, name=name, email=email, role=str(Role.SUPER_ADMIN)),
        role=str(Role.SUPER_ADMIN),
        hashed_password="",
        is_active=True,
        must_change_password=bool(must_change_password),
    )
    password_vault.set_password(user, password)
    db.add(user)
    db.flush()

    audit.record(
        db,
        actor_id=None,
        action=AuditAction.USER_CREATED,
        entity_type=EntityType.USER,
        entity_id=user.id,
        after={
            "name": user.name,
            "email": user.email,
            "role": user.role,
            "is_active": True,
            "must_change_password": user.must_change_password,
            "source": "bootstrap_admin",
        },
    )
    return user


def _read_password() -> str:
    from_env = os.environ.get(PASSWORD_ENV_VAR)
    if from_env:
        return from_env
    if not sys.stdin.isatty():
        raise BootstrapRefused(
            f"No terminal to prompt on. Set {PASSWORD_ENV_VAR} for unattended use."
        )
    first = getpass.getpass("Password for the new Super Admin: ")
    second = getpass.getpass("Repeat the password: ")
    if first != second:
        raise BootstrapRefused("The two passwords did not match.")
    return first


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.seeds.bootstrap_admin")
    parser.add_argument("--email", required=True)
    parser.add_argument("--name", required=True)
    parser.add_argument(
        "--must-change-password",
        action="store_true",
        help="require a password change at first sign-in",
    )
    args = parser.parse_args(argv)

    from sqlalchemy import inspect

    from app.core.config import settings
    from app.db.session import SessionLocal, engine

    if not inspect(engine).has_table("users"):
        print(
            "The database has no schema yet. Run: python -m app.db.migrate upgrade",
            file=sys.stderr,
        )
        return 1

    try:
        with SessionLocal() as db:
            # Checked before prompting, so nobody types a password for nothing.
            if active_super_admin_exists(db):
                raise BootstrapRefused(
                    "An active Super Admin already exists. Nothing was changed."
                )
            password = _read_password()
            user = bootstrap(
                db,
                email=args.email,
                name=args.name,
                password=password,
                must_change_password=args.must_change_password,
            )
            db.commit()
            print(
                f"Created Super Admin {user.email} on {settings.safe_database_url}"
                + (" (password change required at first sign-in)" if user.must_change_password else "")
            )
    except BootstrapRefused as exc:
        print(f"Refusing: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
