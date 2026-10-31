"""Local development helpers for this PC.

    python -m app.seeds.dev_accounts --remove
                                    delete the old test sign-ins superadmin-dev,
                                    shail-dev, navya-dev and parth-dev from the
                                    LOCAL database
    python -m app.seeds.dev_accounts --local-passwords
                                    give every REAL account in the local
                                    database a password you know

The "(dev)" test accounts are gone (the owner asked for them to be removed,
2026-10-30): local testing uses the real accounts. Both commands refuse to
run unless ENV=development is set on purpose, the process is not on Render,
and DATABASE_URL is on this machine (app/core/environment_guard.py) - so
neither can ever touch production.
"""
from __future__ import annotations

import argparse
import sys

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core import password_vault
from app.core.config import settings
from app.core.constants import AuditAction, EntityType
from app.core.environment_guard import require_local_development
from app.db.base import utcnow
from app.models.org import User
from app.services import audit

#: The test sign-ins earlier versions created. Removed, never re-created.
OLD_DEV_USERNAMES: tuple[str, ...] = ("superadmin-dev", "shail-dev", "navya-dev", "parth-dev")


def remove_dev_accounts(db: Session) -> list[str]:
    """Delete the old test accounts. Their sessions go with them; audit
    entries they wrote stay, with no actor. Returns the usernames removed.
    Commits nothing."""
    removed: list[str] = []
    users = db.execute(select(User).where(User.username.in_(OLD_DEV_USERNAMES))).scalars().all()
    ids = {u.id for u in users}
    # Reporting lines between them first, so no delete waits on another.
    for user in users:
        if user.manager_id in ids:
            user.manager_id = None
    db.flush()
    for user in users:
        audit.record(
            db,
            actor_id=None,
            action=AuditAction.USER_DELETED,
            entity_type=EntityType.USER,
            entity_id=user.id,
            before={"name": user.name, "username": user.username, "role": user.role},
            after={"source": "dev_accounts --remove"},
        )
        removed.append(user.username)
        db.delete(user)
    db.flush()
    return sorted(removed)


def set_local_passwords(db: Session, pattern: str) -> list[str]:
    """Give every active account the password
    `pattern` with {username} filled in, unlocked and without a forced
    change. LOCAL database only - main() enforces that. The local copy of
    the data carries whatever passwords it had when it was copied; this makes
    it usable with passwords you know. Returns the usernames, never the
    passwords. Commits nothing."""
    changed: list[str] = []
    users = db.execute(select(User).where(User.is_active.is_(True))).scalars().all()
    for user in users:
        if not user.username:
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
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument(
        "--remove", action="store_true",
        help="delete the old test accounts (superadmin-dev, shail-dev, navya-dev, "
        "parth-dev) from the LOCAL database",
    )
    group.add_argument(
        "--local-passwords", action="store_true",
        help="give every real account in the LOCAL database the password "
        "DEV_LOCAL_PASSWORD_PATTERN (backend/.env) with {username} filled in",
    )
    args = parser.parse_args(argv)

    require_local_development("dev_accounts")
    from app.db.session import SessionLocal

    if args.remove:
        with SessionLocal() as db:
            removed = remove_dev_accounts(db)
            db.commit()
        print(f"Removed {len(removed)} test account(s): {', '.join(removed) or 'none left'}")
        return 0

    pattern = settings.DEV_LOCAL_PASSWORD_PATTERN.strip()
    if "{username}" not in pattern or len(pattern.replace("{username}", "")) < 3:
        print(
            "Refusing: set DEV_LOCAL_PASSWORD_PATTERN in backend/.env, containing "
            "{username} (for example {username} plus a suffix of your own).",
            file=sys.stderr,
        )
        return 1
    with SessionLocal() as db:
        changed = set_local_passwords(db, pattern)
        db.commit()
    print(f"Local passwords set for {len(changed)} account(s): {', '.join(changed)}")
    print("Pattern: DEV_LOCAL_PASSWORD_PATTERN in backend/.env (this PC only).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
