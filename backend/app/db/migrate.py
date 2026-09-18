"""Plain-SQL migration runner.

Why not Alembic: the schema is hand-written SQL that a DBA can read, review
and hand to a change-control process. Alembic's autogenerate would make the
Python models the source of truth and reduce the SQL to a build artefact,
which is the opposite of what is wanted here. What Alembic gives that this
does not - autogenerate and downgrades - is deliberately traded away for
migrations that are exactly the statements that will run in production.

Rules
  1. A migration that has been applied is immutable. Its checksum is stored;
     editing it afterwards is refused. Change the schema by adding a new
     numbered file.
  2. Files apply in filename order, each inside its own transaction.
  3. The same files render for SQLite and PostgreSQL (app/db/render.py).

Usage
    python -m app.db.migrate upgrade
    python -m app.db.migrate status
    python -m app.db.migrate check            # exit 1 if pending/modified (CI/deploy)
    python -m app.db.migrate render --dialect postgresql --out schema.pg.sql
    python -m app.db.migrate reset            # dev SQLite only, drops the file
"""
from __future__ import annotations

import argparse
import hashlib
import sys
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.engine import Engine

from app.db.render import (
    migration_files,
    normalise_dialect,
    render,
    split_statements,
    version_of,
)

# Bookkeeping table. Created outside the migration stream because it is the
# thing that records the migration stream.
_TRACKING_DDL = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    version    VARCHAR(20)  PRIMARY KEY,
    filename   VARCHAR(255) NOT NULL,
    checksum   VARCHAR(64)  NOT NULL,
    applied_at TIMESTAMP    NOT NULL
)
"""


def checksum(path: Path) -> str:
    """Hash the file's text, before rendering, so it is dialect independent.

    Line endings are normalised first: Git stores these files with LF but a
    Windows checkout may carry CRLF, and a migration applied from one and
    checked from the other is still the same migration. Without this, a
    database migrated on a developer's PC refused to start on a Linux host.
    """
    return hashlib.sha256(_normalised(path)).hexdigest()


def _normalised(path: Path) -> bytes:
    return path.read_bytes().replace(b"\r\n", b"\n")


def _legacy_checksums(path: Path) -> set[str]:
    """Checksums an older version of this runner may have stored for the
    same file: the raw bytes as they were on that machine, either ending."""
    text_lf = _normalised(path)
    return {
        hashlib.sha256(text_lf).hexdigest(),
        hashlib.sha256(text_lf.replace(b"\n", b"\r\n")).hexdigest(),
    }


def matches(stored: str, path: Path) -> bool:
    """Does the stored checksum belong to this file, whatever the line endings
    were when it was applied?"""
    return stored == checksum(path) or stored in _legacy_checksums(path)


def ensure_tracking_table(engine: Engine) -> None:
    with engine.begin() as conn:
        conn.execute(text(_TRACKING_DDL))


def applied_migrations(engine: Engine) -> dict[str, str]:
    """version -> checksum for everything already applied."""
    ensure_tracking_table(engine)
    with engine.connect() as conn:
        rows = conn.execute(text("SELECT version, checksum FROM schema_migrations")).all()
    return {row[0]: row[1] for row in rows}


def pending(engine: Engine) -> list[Path]:
    done = applied_migrations(engine)
    out: list[Path] = []
    for path in migration_files():
        version = version_of(path)
        if version not in done:
            out.append(path)
            continue
        if not matches(done[version], path):
            raise RuntimeError(
                f"Migration {path.name} has changed since it was applied.\n"
                "Applied migrations are immutable - the database has already "
                "run the old text.\nAdd a new numbered .sql file with the "
                "change instead of editing this one."
            )
    return out


@contextmanager
def _migration_connection(engine: Engine):
    """A transaction to run one migration in.

    On PostgreSQL this is just `engine.begin()`. On SQLite it additionally
    suspends foreign keys for the duration - see the note at the call site -
    and verifies referential integrity before committing.
    """
    if engine.dialect.name != "sqlite":
        with engine.begin() as conn:
            yield conn
        return

    with engine.connect() as conn:
        # Straight at the driver, deliberately. Going through SQLAlchemy
        # autobegins a transaction BEFORE the pragma runs, and `PRAGMA
        # foreign_keys` is a documented no-op inside a transaction - it would
        # appear to work and change nothing, which is the worst of both.
        driver = conn.connection.driver_connection
        driver.execute("PRAGMA foreign_keys=OFF")

        transaction = conn.begin()
        try:
            yield conn
            orphans = conn.exec_driver_sql("PRAGMA foreign_key_check").fetchall()
            if orphans:
                raise RuntimeError(
                    "Migration left orphaned rows and was rolled back: "
                    f"{orphans[:5]}"
                )
            transaction.commit()
        except Exception:
            transaction.rollback()
            raise
        finally:
            driver.execute("PRAGMA foreign_keys=ON")


def upgrade(engine: Engine, *, verbose: bool = True) -> list[str]:
    """Apply every pending migration. Returns the versions applied."""
    dialect = normalise_dialect(engine.dialect.name)
    todo = pending(engine)
    applied: list[str] = []

    for path in todo:
        version = version_of(path)
        statements = split_statements(render(path.read_text(encoding="utf-8"), dialect))
        # One transaction per migration: a file either lands whole or not at
        # all. SQLite runs DDL transactionally, so this holds on both.
        #
        # Foreign keys are turned OFF around it on SQLite, and this is not
        # optional. Changing a CHECK constraint there means rebuilding the
        # table - create, copy, DROP the original, rename - and with foreign
        # keys ON, dropping a parent CASCADES INTO ITS CHILDREN. Rebuilding
        # `leads` silently deleted every row of `lead_activities`: the whole
        # timeline, gone, with the migration reporting success.
        #
        # This is the procedure SQLite's own documentation prescribes for the
        # table-rebuild pattern. The pragma has to sit OUTSIDE the
        # transaction, because inside one it is a no-op - which is exactly
        # why putting it at the top of the .sql file would not have worked.
        #
        # `foreign_key_check` afterwards is the safety net: if a rebuild left
        # a child pointing at a row that no longer exists, this refuses the
        # migration instead of committing a broken database.
        with _migration_connection(engine) as conn:
            for statement in statements:
                conn.execute(text(statement))
            conn.execute(
                text(
                    "INSERT INTO schema_migrations (version, filename, checksum, applied_at) "
                    "VALUES (:v, :f, :c, :t)"
                ),
                {
                    "v": version,
                    "f": path.name,
                    "c": checksum(path),
                    # An ISO string, not a datetime object: passing a datetime
                    # through raw SQL uses sqlite3's default adapter, which is
                    # deprecated from Python 3.12. Both dialects parse this.
                    "t": datetime.now(timezone.utc)
                    .replace(tzinfo=None)
                    .isoformat(sep=" ", timespec="seconds"),
                },
            )
        applied.append(version)
        if verbose:
            print(f"  applied {path.name} ({len(statements)} statements)")

    if verbose and not applied:
        print("  database is up to date")
    return applied


def status(engine: Engine) -> None:
    done = applied_migrations(engine)
    print(f"dialect: {engine.dialect.name}")
    for path in migration_files():
        version = version_of(path)
        if version not in done:
            state = "PENDING"
        elif not matches(done[version], path):
            state = "MODIFIED AFTER APPLY (error)"
        else:
            state = "applied"
        print(f"  {version}  {path.name:<40} {state}")


def check(engine: Engine) -> int:
    """0 when every migration is applied and unmodified; 1 otherwise.

    Strictly read-only: a missing tracking table means everything is pending.
    """
    from sqlalchemy import inspect

    done: dict[str, str] = {}
    if inspect(engine).has_table("schema_migrations"):
        with engine.connect() as conn:
            rows = conn.execute(text("SELECT version, checksum FROM schema_migrations")).all()
        done = {row[0]: row[1] for row in rows}

    todo: list[Path] = []
    modified: list[str] = []
    for path in migration_files():
        version = version_of(path)
        if version not in done:
            todo.append(path)
        elif not matches(done[version], path):
            modified.append(path.name)
    if modified:
        print(
            "migration check FAILED: applied migration(s) modified since they "
            "were applied: " + ", ".join(modified),
            file=sys.stderr,
        )
        return 1
    if todo:
        print(
            f"migration check FAILED: {len(todo)} pending migration(s): "
            + ", ".join(p.name for p in todo),
            file=sys.stderr,
        )
        return 1
    print("migration check ok: database is up to date")
    return 0


def render_to(dialect: str, out: Path | None) -> str:
    """Concatenate every migration rendered for one dialect.

    This is what you hand to a DBA, or diff against a production database.
    """
    header = (
        f"-- Rendered for {normalise_dialect(dialect)} by app/db/migrate.py\n"
        f"-- Generated {datetime.now(timezone.utc).isoformat(timespec='seconds')}\n"
        "-- Do not edit: edit app/db/sql/*.sql and re-render.\n\n"
    )
    parts = [header]
    for path in migration_files():
        parts.append(f"-- ===== {path.name} =====\n")
        parts.append(render(path.read_text(encoding="utf-8"), dialect).rstrip() + "\n\n")
    sql = "".join(parts)
    if out is not None:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(sql, encoding="utf-8")
        print(f"wrote {out}")
    return sql


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.db.migrate")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("upgrade", help="apply pending migrations")
    sub.add_parser("status", help="show applied and pending migrations")
    sub.add_parser(
        "check",
        help="exit 0 if the database is fully migrated, non-zero if anything "
        "is pending or an applied file was modified (for deploy pipelines)",
    )
    sub.add_parser("reset", help="DEV ONLY: delete the SQLite file and re-apply")
    render_cmd = sub.add_parser("render", help="print the DDL for a dialect")
    render_cmd.add_argument("--dialect", default="postgresql")
    render_cmd.add_argument("--out", type=Path, default=None)

    args = parser.parse_args(argv)

    if args.command == "render":
        sql = render_to(args.dialect, args.out)
        if args.out is None:
            print(sql)
        return 0

    # Imported here so `render` works without a configured database.
    from app.core.config import settings
    from app.db.session import build_engine

    # No statement timeout for DDL: a long ALTER must not be cancelled halfway
    # through a deploy.
    engine = build_engine(settings.DATABASE_URL, statement_timeout_ms=0)
    try:
        return _dispatch(args.command, engine, settings)
    finally:
        engine.dispose()


def _dispatch(command: str, engine: Engine, settings) -> int:  # noqa: ANN001
    if command == "upgrade":
        print(f"upgrading {settings.safe_database_url}")
        upgrade(engine)
        return 0

    if command == "status":
        status(engine)
        return 0

    if command == "check":
        return check(engine)

    if command == "reset":
        if engine.dialect.name != "sqlite" or settings.is_production:
            print(
                "reset is refused: it only runs against a SQLite database "
                "outside production.",
                file=sys.stderr,
            )
            return 1
        engine.dispose()
        db_path = Path(engine.url.database or "")
        for suffix in ("", "-wal", "-shm"):
            candidate = Path(str(db_path) + suffix)
            if candidate.exists():
                candidate.unlink()
                print(f"  removed {candidate}")
        upgrade(engine)
        return 0

    return 1


if __name__ == "__main__":
    raise SystemExit(main())
