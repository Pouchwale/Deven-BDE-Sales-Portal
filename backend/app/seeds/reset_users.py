"""Set new passwords for named accounts - safely, in production.

This is the sanctioned "I need to set some people's passwords now" tool. It
does exactly what the admin panel's reset does, for a list of accounts, from a
terminal:

  * finds each account by username or email (NEVER creates one),
  * hashes the new password and writes the same encrypted copy the rest of the
    app keeps (app/core/password_vault.py) - no plaintext column,
  * stamps password_changed_at, which by itself signs out every older session
    (app/core/deps.py), and revokes the session rows too,
  * writes a PASSWORD_RESET audit row (who, whom, when - never the password),
  * commits each account on its own, so one failure does not undo the rest,
  * then re-reads the row and PROVES the new password verifies.

What it deliberately does NOT do:

  * take a password on the command line (argv is visible to every process and
    lands in shell history) - passwords are typed at a getpass prompt, or read
    from a file you point at with --from-file and delete afterwards, or from
    RESET_PW_<USERNAME> environment variables;
  * print, log or audit any password or hash;
  * create, delete, deactivate or rename anyone, or touch any account you did
    not name;
  * reset "everyone" - there is no such option here on purpose.

Usage
    python -m app.seeds.reset_users --users shail,navya,parth --force-change
    python -m app.seeds.reset_users --users superadmin --from-file /tmp/pw.json
    RESET_PW_NAVYA=... python -m app.seeds.reset_users --users navya --from-env

--from-file reads a JSON object {"username": "new password", ...}; the file is
never echoed and should be deleted after the run (it holds live credentials).

Flags
    --force-change   require each person to choose a new password at next sign-in
    --yes            do not ask for confirmation (unattended runs)
"""
from __future__ import annotations

import argparse
import getpass
import json
import os
import sys
from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.orm import Session

import app.models  # noqa: F401  (registers every mapper)
from app.core import password_vault
from app.core.config import settings
from app.core.constants import AuditAction, EntityType
from app.core.passwords import PasswordPolicyError, validate_password
from app.core.sessions import RevokeReason, revoke_user_sessions
from app.core.security import verify_password
from app.core.usernames import normalise_login
from app.db.base import utcnow
from app.db.session import SessionLocal, engine
from app.models.org import User
from app.services import audit


class ResetRefused(RuntimeError):
    """Safe to print: never contains a password."""


def _find_user(db: Session, identifier: str) -> User | None:
    """By email when it looks like one, otherwise by username. Never creates."""
    ident = normalise_login(identifier)
    lookup = func.lower(User.email) == ident if "@" in ident else User.username == ident
    return db.execute(select(User).where(lookup)).scalar_one_or_none()


def _read_password_for(identifier: str, source: dict[str, str] | None) -> str:
    """The new password for one account, from the chosen source. Never logged."""
    if source is not None:
        key = normalise_login(identifier)
        # Match on the exact identifier, or its username/local-part form.
        for candidate in (identifier, key, key.split("@", 1)[0]):
            if candidate in source:
                return source[candidate]
        raise ResetRefused(f"No password supplied for {identifier!r}.")
    if not sys.stdin.isatty():
        raise ResetRefused(
            f"No terminal to prompt on for {identifier!r}. Use --from-file or "
            "--from-env for unattended runs."
        )
    first = getpass.getpass(f"New password for {identifier}: ")
    second = getpass.getpass("Repeat it: ")
    if first != second:
        raise ResetRefused(f"The two passwords for {identifier!r} did not match.")
    return first


def _env_source(identifiers: list[str]) -> dict[str, str]:
    """Passwords from RESET_PW_<USERNAME> variables, keyed by the identifier."""
    out: dict[str, str] = {}
    for ident in identifiers:
        var = "RESET_PW_" + normalise_login(ident).split("@", 1)[0].upper()
        value = os.environ.get(var)
        if value:
            out[ident] = value
    return out


@dataclass
class Outcome:
    identifier: str
    found: bool
    updated: bool
    sessions_revoked: int


def reset_one(
    db: Session,
    user: User,
    new_password: str,
    *,
    actor_id=None,
    force_change: bool,
) -> Outcome:
    """Set one account's password exactly as the admin panel's reset does, then
    verify the stored hash actually accepts the new password. Commits itself."""
    validate_password(new_password, email=user.email, name=user.name)

    password_vault.set_password(user, new_password)
    user.password_changed_at = utcnow()
    user.failed_login_count = 0
    user.locked_until = None
    if force_change:
        user.must_change_password = True
    db.flush()

    ended = revoke_user_sessions(db, user.id, RevokeReason.PASSWORD_RESET)

    audit.record(
        db,
        actor_id=actor_id,
        action=AuditAction.PASSWORD_RESET,
        entity_type=EntityType.USER,
        entity_id=user.id,
        after={"source": "reset_users", "sessions_revoked": ended, "force_change": force_change},
    )
    db.commit()

    # Prove it against a fresh read, not the in-memory object.
    db.expire(user)
    reloaded = db.get(User, user.id)
    updated = reloaded is not None and verify_password(new_password, reloaded.hashed_password)
    return Outcome(user.username or user.email, True, bool(updated), ended)


def run(identifiers: list[str], *, source: dict[str, str] | None, force_change: bool) -> list[Outcome]:
    outcomes: list[Outcome] = []
    with SessionLocal() as db:
        for ident in identifiers:
            user = _find_user(db, ident)
            if user is None:
                outcomes.append(Outcome(ident, False, False, 0))
                print(f"  {ident:<24} NOT FOUND - skipped (nothing created)")
                continue
            try:
                password = _read_password_for(ident, source)
                validate_password(password, email=user.email, name=user.name)
            except (ResetRefused, PasswordPolicyError) as exc:
                db.rollback()
                outcomes.append(Outcome(ident, True, False, 0))
                print(f"  {ident:<24} SKIPPED - {exc}")
                continue
            outcome = reset_one(db, user, password, force_change=force_change)
            del password
            flag = " (must change at next sign-in)" if force_change else ""
            print(
                f"  {outcome.identifier:<24} hash updated: "
                f"{'YES' if outcome.updated else 'NO'}  committed: YES  "
                f"sessions revoked: {outcome.sessions_revoked}{flag}"
            )
            outcomes.append(outcome)
    return outcomes


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.seeds.reset_users")
    parser.add_argument(
        "--users", required=True,
        help="comma-separated usernames or emails to reset (only these are touched)",
    )
    parser.add_argument(
        "--from-file",
        help="JSON object {username: new_password}; read once, never echoed",
    )
    parser.add_argument(
        "--from-env", action="store_true",
        help="read each password from RESET_PW_<USERNAME> instead of prompting",
    )
    parser.add_argument(
        "--force-change", action="store_true",
        help="require each person to set a new password at next sign-in",
    )
    parser.add_argument("--yes", action="store_true", help="skip the confirmation prompt")
    args = parser.parse_args(argv)

    identifiers = [u.strip() for u in args.users.split(",") if u.strip()]
    if not identifiers:
        print("Refusing: --users is empty.", file=sys.stderr)
        return 1

    from sqlalchemy import inspect

    if not inspect(engine).has_table("users"):
        print(
            "The database has no schema yet. Run: python -m app.db.migrate upgrade",
            file=sys.stderr,
        )
        return 1

    source: dict[str, str] | None = None
    if args.from_file:
        try:
            with open(args.from_file, encoding="utf-8") as handle:
                loaded = json.load(handle)
        except (OSError, json.JSONDecodeError) as exc:
            print(f"Refusing: could not read --from-file ({type(exc).__name__}).", file=sys.stderr)
            return 1
        if not isinstance(loaded, dict) or not all(isinstance(v, str) for v in loaded.values()):
            print("Refusing: --from-file must be a JSON object of username -> password.", file=sys.stderr)
            return 1
        source = {str(k): v for k, v in loaded.items()}
    elif args.from_env:
        source = _env_source(identifiers)

    # safe_database_url masks any password in the URL.
    print(f"Target database: {settings.safe_database_url}")
    print(f"Accounts to reset ({len(identifiers)}): {', '.join(identifiers)}")
    if args.force_change:
        print("Each will be required to change it at next sign-in.")
    if not args.yes:
        if not sys.stdin.isatty():
            print("Refusing: not a terminal and --yes not given.", file=sys.stderr)
            return 1
        if input("Proceed? [y/N] ").strip().lower() not in ("y", "yes"):
            print("Aborted. Nothing changed.")
            return 1

    try:
        outcomes = run(identifiers, source=source, force_change=args.force_change)
    except ResetRefused as exc:
        print(f"Refusing: {exc}", file=sys.stderr)
        return 1
    finally:
        # Do not let the plaintext linger in the process.
        if source is not None:
            source.clear()

    failures = [o for o in outcomes if not o.found or not o.updated]
    done = len([o for o in outcomes if o.updated])
    print(f"\nDone: {done} reset, {len(failures)} skipped/failed.")
    return 0 if not failures else 2


if __name__ == "__main__":
    raise SystemExit(main())
