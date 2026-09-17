"""Give every account that has none a sign-in username.

Safe to run repeatedly and in production: it only fills blanks, never renames
anybody, and touches no password.

    python -m app.seeds.assign_usernames
"""
from __future__ import annotations

import sys

from app.core.config import settings
from app.core.usernames import assign_missing_usernames
from app.db.session import SessionLocal


def main() -> int:
    print(f"assigning usernames on {settings.safe_database_url}")
    with SessionLocal() as db:
        assigned = assign_missing_usernames(db)
        db.commit()
    for name, username in assigned:
        print(f"  {username:20} {name}")
    print(f"  {len(assigned)} assigned")
    return 0


if __name__ == "__main__":
    sys.exit(main())
