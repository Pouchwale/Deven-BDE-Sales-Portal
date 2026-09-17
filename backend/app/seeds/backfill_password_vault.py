"""Fill in the revealable password for accounts that still use a known one.

Existing passwords are one-way hashes and cannot be read back. The one case
that CAN be filled in honestly is an account still on a password we already
know - the shared SEED_PASSWORD - which is proven by checking it against the
stored hash. Everyone else shows nothing until their password is next set.

Never prints a password.

    python -m app.seeds.backfill_password_vault
"""
from __future__ import annotations

import sys

from sqlalchemy import select

import app.models  # noqa: F401  (registers every mapper)
from app.core import password_vault
from app.core.config import settings
from app.core.security import verify_password
from app.db.session import SessionLocal
from app.models.org import User


def main() -> int:
    if not password_vault.enabled():
        print("PASSWORD_VIEW_KEY is not set - nothing can be stored.", file=sys.stderr)
        return 1
    known = settings.SEED_PASSWORD
    filled = unknown = 0
    with SessionLocal() as db:
        users = db.execute(select(User).where(User.password_encrypted.is_(None))).scalars()
        for user in users:
            if known and verify_password(known, user.hashed_password):
                user.password_encrypted = password_vault.encrypt(known)
                filled += 1
            else:
                unknown += 1
        db.commit()
    print(f"filled {filled}; {unknown} unknown (shown once their password is next set)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
