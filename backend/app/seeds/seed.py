"""Seed the organisation and import the real SAP customer data.

Idempotent: run it as often as you like. Users are matched on email, teams
and departments on code, customers and invoice lines on their SAP identity.
A second run creates nothing and reports zero.

What this seeds
  * 3 teams, 6 departments, 22 user accounts with the reporting tree
  * runtime settings
  * converted customers and invoice lines, read from SAP_DATA_FILE (or data/sap/)

What this deliberately does NOT seed
  * leads, references and feedback. There is no invented transactional data
    anywhere in this project - lead and reference activity is created by
    people using the portal, and feedback comes from the real Google Forms
    export once it is supplied.

    python -m app.seeds.seed
    python -m app.seeds.seed --skip-sap
    python -m app.seeds.seed --reset-passwords   # undo an E2E run's changes

Production (ENV=production) refuses unless --allow-production is passed, and
even then seeds only teams, departments and settings. The first account there
comes from `python -m app.seeds.bootstrap_admin`.
"""
from __future__ import annotations

import argparse
import sys

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core import password_vault
from app.core.usernames import assign_missing_usernames
from app.db.base import utcnow
from app.db.session import SessionLocal, engine
from app.models.customer import Customer
from app.models.org import Department, Team, User
from app.models.system import AppSetting
from app.seeds.roster import DEFAULT_SETTINGS, DEPARTMENTS, ROSTER, TEAMS
from app.services.customer_source import FileCustomerSource
from app.services.sap_import import import_source


class SeedRefused(RuntimeError):
    """The seed will not do what was asked, for a reason worth reading."""


def seed_teams(db: Session) -> dict[str, Team]:
    existing = {t.code: t for t in db.execute(select(Team)).scalars()}
    created = 0
    for name, code, order in TEAMS:
        if code not in existing:
            team = Team(name=name, code=str(code), sort_order=order)
            db.add(team)
            existing[str(code)] = team
            created += 1
    db.flush()
    print(f"  teams:        {created} created, {len(existing)} total")
    return existing


def seed_departments(db: Session) -> dict[str, Department]:
    existing = {d.code: d for d in db.execute(select(Department)).scalars()}
    created = 0
    for name, code, order in DEPARTMENTS:
        if code not in existing:
            department = Department(name=name, code=code, sort_order=order)
            db.add(department)
            existing[code] = department
            created += 1
    db.flush()
    print(f"  departments:  {created} created, {len(existing)} total")
    return existing


def seed_users(db: Session, teams: dict[str, Team]) -> dict[str, User]:
    """Two passes: create everyone, then wire the reporting lines.

    One pass would need the roster sorted so a manager always precedes their
    reports, which makes the roster fragile to edit.
    """
    if settings.is_production:
        # Belt and braces: `run` never calls this in production. A roster of
        # accounts sharing one known password is a development fixture.
        raise SeedRefused(
            "Refusing to create roster users in production. Create the first "
            "administrator with: python -m app.seeds.bootstrap_admin"
        )

    by_email = {u.email: u for u in db.execute(select(User)).scalars()}
    by_name: dict[str, User] = {}
    created = 0

    missing = [e for e in ROSTER if e.resolved_email not in by_email]
    if missing and not settings.SEED_PASSWORD.strip():
        raise SeedRefused(
            f"SEED_PASSWORD is not set, and {len(missing)} roster account(s) "
            "need creating. Set SEED_PASSWORD in backend/.env (development "
            "only) or run with --skip-users."
        )

    for entry in ROSTER:
        email = entry.resolved_email
        user = by_email.get(email)
        if user is None:
            user = User(
                name=entry.name,
                email=email,
                role=str(entry.role),
                title=entry.title,
                team_id=teams[entry.team_code].id if entry.team_code else None,
                hashed_password="",
                is_active=True,
                # Off during development (SEED_FORCE_PASSWORD_CHANGE) so
                # signing in is one click. When it is on, the account is not
                # really theirs until they replace the shared password.
                must_change_password=settings.SEED_FORCE_PASSWORD_CHANGE,
            )
            password_vault.set_password(user, settings.SEED_PASSWORD)
            db.add(user)
            by_email[email] = user
            created += 1
        by_name[entry.name] = user
    db.flush()

    lines_set = 0
    for entry in ROSTER:
        user = by_name[entry.name]
        manager = by_name.get(entry.manager) if entry.manager else None
        manager_id = manager.id if manager else None
        if user.manager_id != manager_id:
            user.manager_id = manager_id
            lines_set += 1
    db.flush()

    print(f"  users:        {created} created, {len(by_email)} total, "
          f"{lines_set} reporting line(s) set")
    return by_name


def seed_settings(db: Session) -> None:
    existing = {s.key for s in db.execute(select(AppSetting)).scalars()}
    # The .env values win on first seed; after that the admin panel owns them.
    env_defaults = {
        "feedback.rating_scale_max": str(settings.FEEDBACK_RATING_SCALE_MAX),
        "feedback.alert_threshold": str(settings.FEEDBACK_ALERT_THRESHOLD),
        "feedback.alert_min_responses": str(settings.FEEDBACK_ALERT_MIN_RESPONSES),
        "feedback.alert_window_days": str(settings.FEEDBACK_ALERT_WINDOW_DAYS),
    }
    created = 0
    for key, value_type, default, description in DEFAULT_SETTINGS:
        if key in existing:
            continue
        db.add(
            AppSetting(
                key=key,
                # `or default`, not `.get(key, default)`: an unset variable in
                # .env arrives as an empty string, which would otherwise blank
                # out a perfectly good default.
                value=env_defaults.get(key) or default,
                value_type=value_type,
                description=description,
            )
        )
        created += 1
    db.flush()
    print(f"  settings:     {created} created")


def seed_sap_customers(db: Session) -> None:
    """Import the SAP workbook named by SAP_DATA_FILE, else every export in data/sap/.

    Real data, byte-for-byte as exported. Ownership comes from the file's own
    Sales Person column - nothing is assigned by hand here.
    """
    files = [p for p in settings.sap_data_files if p.exists()]
    if not files:
        print(
            "  customers:    no SAP export found at "
            f"{settings.SAP_DATA_FILE or settings.sap_data_dir}"
        )
        return

    for path in files:
        result = import_source(db, FileCustomerSource(path))
        print(
            f"  customers:    {path.name} -> "
            f"{result.customers_created} customer(s) created, "
            f"{result.lines_created} invoice line(s) created, "
            f"{result.lines_skipped} skipped, {result.error_count} error(s)"
        )
        if result.unmatched_sales_people:
            print(
                "                unmatched sales people (left unowned): "
                + ", ".join(sorted(result.unmatched_sales_people))
            )
        for error in result.errors[:5]:
            print(f"                row {error['row']}: {error['message']}")


def reset_passwords(db: Session) -> int:
    """Put every account back on SEED_PASSWORD, with the forced change re-armed.

    The end-to-end suite signs in and completes the forced password change --
    that is the journey it is testing -- which leaves those accounts on a
    password nobody wrote down. Rather than making the test tiptoe around the
    thing it exists to exercise, this undoes it in one command.

    Setting password_changed_at also invalidates every token issued before
    now, so a browser left open on an old session is signed out rather than
    lingering with stale access.
    """
    users = list(db.execute(select(User)).scalars().all())
    for user in users:
        password_vault.set_password(user, settings.SEED_PASSWORD)
        user.must_change_password = settings.SEED_FORCE_PASSWORD_CHANGE
        user.password_changed_at = utcnow()
    db.flush()
    return len(users)


def summarise(db: Session) -> None:
    user_count = db.scalar(select(func.count(User.id))) or 0
    customer_count = db.scalar(select(func.count(Customer.id))) or 0
    owned = db.scalar(
        select(func.count(Customer.id)).where(Customer.owner_user_id.is_not(None))
    ) or 0

    print("\n  --- summary -------------------------------------------------")
    print(f"  users                {user_count}")
    print(f"  customers            {customer_count} ({owned} owned, "
          f"{customer_count - owned} unowned)")
    if settings.is_production:
        print("  ---------------------------------------------------------------")
        return
    print("\n  Sign in with any seeded email, for example:")
    print("    owner@pouchwale.com          (Super Admin)")
    print("    shail.patel@pouchwale.com    (Admin)")
    print("    navya.rupawat@pouchwale.com  (Manager, BDE team)")
    print("    parth.fulvani@pouchwale.com  (BDE)")
    # The password itself is not echoed: it is already in backend/.env, and
    # terminal output ends up in logs and screenshots.
    print("\n  Password for every seeded account: the SEED_PASSWORD value in backend/.env")
    if settings.SEED_FORCE_PASSWORD_CHANGE:
        print("  Every account must change it at first sign-in.")
    print("  ---------------------------------------------------------------")


def run(*, skip_sap: bool = False, skip_users: bool = False) -> None:
    """Seed everything this environment allows.

    In production only teams, departments and settings are seeded: no roster
    accounts (users come from `app.seeds.bootstrap_admin` and the admin UI)
    and no SAP import (the running app's SAP sync owns that).
    """
    print(f"seeding {settings.safe_database_url}")
    production = settings.is_production
    with SessionLocal() as db:
        teams = seed_teams(db)
        seed_departments(db)
        if production:
            print("  users:        skipped (production - use app.seeds.bootstrap_admin)")
        elif skip_users:
            print("  users:        skipped (--skip-users)")
        else:
            seed_users(db, teams)
            assigned = assign_missing_usernames(db)
            if assigned:
                print(f"  usernames:    {len(assigned)} assigned")
        seed_settings(db)
        if production:
            print("  customers:    skipped (production - SAP sync imports them)")
        elif not skip_sap:
            seed_sap_customers(db)
        db.commit()
        summarise(db)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.seeds.seed")
    parser.add_argument(
        "--skip-sap", action="store_true", help="seed the org only, no customer import"
    )
    parser.add_argument(
        "--reset-passwords",
        action="store_true",
        help="put every account back on SEED_PASSWORD (use after an E2E run)",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="allow --reset-passwords against a non-SQLite database (never production)",
    )
    parser.add_argument(
        "--skip-users", action="store_true", help="do not create roster accounts"
    )
    parser.add_argument(
        "--allow-production",
        action="store_true",
        help="permit running with ENV=production (teams, departments and "
        "settings only - never users)",
    )
    args = parser.parse_args(argv)

    if settings.is_production:
        if args.reset_passwords:
            print(
                "Refusing: --reset-passwords never runs in production.",
                file=sys.stderr,
            )
            return 1
        if not args.allow_production:
            print(
                "Refusing to seed with ENV=production.\n"
                "  Pass --allow-production to seed teams, departments and settings\n"
                "  only. Accounts are never seeded in production; create the first\n"
                "  administrator with: python -m app.seeds.bootstrap_admin",
                file=sys.stderr,
            )
            return 1

    # Fail with a clear message rather than a missing-table traceback.
    from sqlalchemy import inspect

    if not inspect(engine).has_table("users"):
        print(
            "The database has no schema yet. Run:\n"
            "    python -m app.db.migrate upgrade",
            file=sys.stderr,
        )
        return 1

    if args.reset_passwords:
        # Resetting every password at once is a development convenience and a
        # terrible thing to do to a live installation, so it refuses to touch
        # anything but the local SQLite file without an explicit --force.
        if not settings.is_sqlite and not args.force:
            print(
                "Refusing to reset every password on a non-SQLite database.\n"
                f"  target: {settings.safe_database_url}\n"
                "  Pass --force if that is genuinely what you want.",
                file=sys.stderr,
            )
            return 1

        if not settings.SEED_PASSWORD.strip():
            print("Refusing: SEED_PASSWORD is not set.", file=sys.stderr)
            return 1

        with SessionLocal() as db:
            count = reset_passwords(db)
            db.commit()
        print(
            f"Reset {count} account(s) to the seed password (SEED_PASSWORD in backend/.env)."
        )
        print(
            "Every account must change it at next sign-in."
            if settings.SEED_FORCE_PASSWORD_CHANGE
            else "Sign-in goes straight to the portal "
            "(SEED_FORCE_PASSWORD_CHANGE is off)."
        )
        return 0

    try:
        run(skip_sap=args.skip_sap, skip_users=args.skip_users)
    except SeedRefused as exc:
        print(f"Refusing: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
