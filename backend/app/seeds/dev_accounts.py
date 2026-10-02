"""Development sign-ins for this PC: superadmin-dev, shail-dev, navya-dev,
parth-dev.

    python -m app.seeds.dev_accounts            create the ones that are missing
    python -m app.seeds.dev_accounts --reset    put all four back on the password

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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.seeds.dev_accounts")
    parser.add_argument(
        "--reset", action="store_true",
        help="put existing dev accounts back on DEV_ACCOUNT_PASSWORD and unlock them",
    )
    args = parser.parse_args(argv)

    require_local_development("dev_accounts")

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
