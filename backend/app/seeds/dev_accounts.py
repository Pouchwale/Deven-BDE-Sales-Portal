"""Development sign-ins for this PC: superadmin-dev, shail-dev, navya-dev,
parth-dev.

    python -m app.seeds.dev_accounts            create the ones that are missing
    python -m app.seeds.dev_accounts --reset    put all four back on the password
    python -m app.seeds.dev_accounts --local-passwords
                                                give every REAL account in the local
                                                database a password you know

Local development must not depend on production passwords. These four cover
every role, all share DEV_ACCOUNT_PASSWORD from backend/.env, and they exist
only in the local database: the command refuses to run unless ENV=development
is set on purpose, the process is not on Render, and DATABASE_URL is on this
machine (app/core/environment_guard.py).

Existing accounts are left exactly as they are unless --reset is passed, so a
password changed while testing survives a re-run - and a restart.
deploy/backup/copy-from-render.ps1 runs this after every refresh, because a
copy of production does not contain them.
"""
from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core import password_vault
from app.core.config import settings
from app.core.constants import AuditAction, EntityType, Role
from app.core.environment_guard import require_local_development
from app.db.base import utcnow
from app.models.org import User
from app.services import audit

MIN_PASSWORD_LENGTH = 8


@dataclass(frozen=True)
class DevAccount:
    username: str
    name: str
    role: Role
    #: Whose team (by username) to join, when that person exists locally.
    mirrors: str | None
    manager: str | None


ACCOUNTS: tuple[DevAccount, ...] = (
    DevAccount("superadmin-dev", "Super Admin (dev)", Role.SUPER_ADMIN, None, None),
    DevAccount("shail-dev", "Shail (dev)", Role.ADMIN, "shail", None),
    DevAccount("navya-dev", "Navya (dev)", Role.MANAGER, "navya", "shail-dev"),
    DevAccount("parth-dev", "Parth (dev)", Role.BDE, "parth", "navya-dev"),
)


def ensure(db: Session, password: str, *, reset: bool = False) -> dict[str, str]:
    """Create missing accounts (and, with reset, restore the password).
    Returns {username: created|reset|kept}. Commits nothing."""
    outcome: dict[str, str] = {}
    by_username: dict[str, User] = {}

    for spec in ACCOUNTS:
        user = db.execute(select(User).where(User.username == spec.username)).scalar_one_or_none()
        mirror = (
            db.execute(select(User).where(User.username == spec.mirrors)).scalar_one_or_none()
            if spec.mirrors
            else None
        )
        if user is None:
            user = User(
                name=spec.name,
                # example.com is reserved: these addresses can never reach anyone.
                email=f"{spec.username}@example.com",
                username=spec.username,
                role=str(spec.role),
                title="Development account",
                team_id=mirror.team_id if mirror else None,
                hashed_password="",
                is_active=True,
                must_change_password=False,
            )
            password_vault.set_password(user, password)
            user.password_changed_at = utcnow()
            db.add(user)
            db.flush()
            audit.record(
                db,
                actor_id=None,
                action=AuditAction.USER_CREATED,
                entity_type=EntityType.USER,
                entity_id=user.id,
                after={"role": str(spec.role), "username": spec.username, "source": "dev_accounts"},
            )
            outcome[spec.username] = "created"
        elif reset:
            password_vault.set_password(user, password)
            user.password_changed_at = utcnow()
            user.is_active = True
            user.deactivated_at = None
            user.locked_until = None
            user.failed_login_count = 0
            user.must_change_password = False
            audit.record(
                db,
                actor_id=None,
                action=AuditAction.PASSWORD_RESET,
                entity_type=EntityType.USER,
                entity_id=user.id,
                after={"source": "dev_accounts"},
            )
            outcome[spec.username] = "reset"
        else:
            outcome[spec.username] = "kept"
        by_username[spec.username] = user

    # Reporting lines, so a manager-dev actually has a report to look at.
    for spec in ACCOUNTS:
        if spec.manager and outcome[spec.username] == "created":
            by_username[spec.username].manager_id = by_username[spec.manager].id
    db.flush()
    return outcome


def set_local_passwords(db: Session, pattern: str) -> list[str]:
    """Give every active real account (not the -dev ones) the password
    `pattern` with {username} filled in, unlocked and without a forced
    change. LOCAL database only - main() enforces that. The local copy of
    the data carries whatever passwords it had when it was copied; this makes
    it usable with passwords you know. Returns the usernames, never the
    passwords. Commits nothing."""
    changed: list[str] = []
    users = db.execute(select(User).where(User.is_active.is_(True))).scalars().all()
    for user in users:
        if not user.username or user.username.endswith("-dev"):
            continue
        password_vault.set_password(user, pattern.replace("{username}", user.username))
        user.password_changed_at = utcnow()
        user.locked_until = None
        user.failed_login_count = 0
        user.must_change_password = False
        audit.record(
            db,
            actor_id=None,
            action=AuditAction.PASSWORD_RESET,
            entity_type=EntityType.USER,
            entity_id=user.id,
            after={"source": "dev_accounts --local-passwords"},
        )
        changed.append(user.username)
    db.flush()
    return sorted(changed)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.seeds.dev_accounts")
    parser.add_argument(
        "--reset", action="store_true",
        help="put existing dev accounts back on DEV_ACCOUNT_PASSWORD and unlock them",
    )
    parser.add_argument(
        "--local-passwords", action="store_true",
        help="give every real account in the LOCAL database the password "
        "DEV_LOCAL_PASSWORD_PATTERN (backend/.env) with {username} filled in",
    )
    args = parser.parse_args(argv)

    require_local_development("dev_accounts")

    if args.local_passwords:
        pattern = settings.DEV_LOCAL_PASSWORD_PATTERN.strip()
        if "{username}" not in pattern or len(pattern.replace("{username}", "")) < 3:
            print(
                "Refusing: set DEV_LOCAL_PASSWORD_PATTERN in backend/.env, containing "
                "{username} (for example {username} plus a suffix of your own).",
                file=sys.stderr,
            )
            return 1
        from app.db.session import SessionLocal

        with SessionLocal() as db:
            changed = set_local_passwords(db, pattern)
            db.commit()
        print(f"Local passwords set for {len(changed)} account(s): {', '.join(changed)}")
        print("Pattern: DEV_LOCAL_PASSWORD_PATTERN in backend/.env (this PC only).")
        return 0

    password = settings.DEV_ACCOUNT_PASSWORD.strip()
    if len(password) < MIN_PASSWORD_LENGTH:
        print(
            f"Refusing: set DEV_ACCOUNT_PASSWORD (at least {MIN_PASSWORD_LENGTH} "
            "characters) in backend/.env.",
            file=sys.stderr,
        )
        return 1

    from app.db.session import SessionLocal

    with SessionLocal() as db:
        outcome = ensure(db, password, reset=args.reset)
        db.commit()
    for username, what in outcome.items():
        print(f"  {username:<16} {what}")
    print("Password: DEV_ACCOUNT_PASSWORD in backend/.env (this PC only).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
