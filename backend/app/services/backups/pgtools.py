"""PostgreSQL's own tools - pg_dump, pg_restore, psql - run safely.

Credentials go to the child process through PG* environment variables, never
argv (argv is visible to every process and lands in logs). Nothing here
returns or logs a connection string.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import unquote

from sqlalchemy.engine import make_url

from app.core.config import settings

PGDMP_MAGIC = b"PGDMP"


class PgToolError(RuntimeError):
    """A tool failed. The message is safe to show (no credentials)."""


@dataclass(frozen=True)
class Target:
    """A database, described without its password."""

    host: str
    port: int
    database: str
    user: str
    password: str
    sslmode: str | None

    @property
    def identity(self) -> str:
        """Safe to show and to audit: host/database, no user or password."""
        return f"{self.host}:{self.port}/{self.database}"

    def env(self) -> dict[str, str]:
        env = dict(os.environ)
        env.update(
            PGHOST=self.host, PGPORT=str(self.port), PGDATABASE=self.database,
            PGUSER=self.user, PGPASSWORD=self.password,
            PGCONNECT_TIMEOUT="15", PGAPPNAME="gp3-backup",
        )
        if self.sslmode:
            env["PGSSLMODE"] = self.sslmode
        else:
            env.pop("PGSSLMODE", None)
        return env


def target_from_url(url: str) -> Target:
    parsed = make_url(url)
    if not parsed.drivername.startswith("postgresql"):
        raise PgToolError("Backups need a PostgreSQL database.")
    query = dict(parsed.query or {})
    sslmode = query.get("sslmode")
    host = parsed.host or "localhost"
    if not sslmode and host.endswith(".render.com"):
        sslmode = "require"
    return Target(
        host=host,
        port=int(parsed.port or 5432),
        database=parsed.database or "",
        user=unquote(parsed.username or ""),
        password=unquote(parsed.password or ""),
        sslmode=str(sslmode) if sslmode else None,
    )


def portal_target() -> Target:
    return target_from_url(settings.DATABASE_URL)


# ------------------------------------------------------------------ tools
def _candidates() -> list[Path]:
    found: list[Path] = []
    if settings.PG_BIN_DIR.strip():
        found.append(Path(settings.PG_BIN_DIR.strip()))
    on_path = shutil.which("pg_dump")
    if on_path:
        found.append(Path(on_path).parent)
    base = Path("C:/Program Files/PostgreSQL")
    if base.exists():
        versions = sorted(
            (p for p in base.iterdir() if p.name.isdigit()), key=lambda p: int(p.name), reverse=True
        )
        found.extend(v / "bin" for v in versions)
    return found


def tool(name: str) -> str:
    exe = name + (".exe" if os.name == "nt" else "")
    for folder in _candidates():
        candidate = folder / exe
        if candidate.exists():
            return str(candidate)
    raise PgToolError(
        f"{name} was not found on this server. Install the PostgreSQL client tools "
        "(the same major version as the database, or newer) or set PG_BIN_DIR."
    )


def tool_major(name: str = "pg_dump") -> int | None:
    try:
        out = subprocess.run([tool(name), "--version"], capture_output=True, text=True, timeout=30).stdout
    except (PgToolError, OSError, subprocess.SubprocessError):
        return None
    match = re.search(r"(\d+)(?:\.\d+)?", out)
    return int(match.group(1)) if match else None


def run(name: str, args: list[str], target: Target | None, *, timeout: int = 3600, extra_env: dict | None = None) -> str:
    """Run a tool; return stdout. Raises PgToolError with the last stderr lines
    (which never contain the password - it is not in argv)."""
    env = target.env() if target else dict(os.environ)
    if extra_env:
        env.update(extra_env)
    try:
        proc = subprocess.run(
            [tool(name), *args], env=env, capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        raise PgToolError(f"{name} took longer than {timeout // 60} minutes and was stopped.") from None
    if proc.returncode != 0:
        tail = " | ".join(line.strip() for line in proc.stderr.strip().splitlines()[-4:] if line.strip())
        raise PgToolError(f"{name} failed (exit {proc.returncode}): {tail or 'no detail'}")
    return proc.stdout


def server_version(target: Target) -> str:
    return run("psql", ["-At", "-c", "show server_version"], target, timeout=60).strip()


def server_major(target: Target) -> int:
    match = re.match(r"(\d+)", server_version(target))
    if not match:
        raise PgToolError("Could not read the database server's version.")
    return int(match.group(1))


def scalar(target: Target, sql: str) -> str:
    return run("psql", ["-At", "-v", "ON_ERROR_STOP=1", "-c", sql], target, timeout=300).strip()


# ------------------------------------------------------------------ dumps
def dump(target: Target, out_file: Path) -> None:
    """A complete custom-format dump: schema, data, constraints, indexes,
    sequences (with their values), views, functions, triggers and extensions
    of the portal's database. Ownership and grants are left out on purpose:
    Render's role is not a superuser, and a restore elsewhere must not try to
    recreate roles that do not exist there (roles are cluster objects)."""
    out_file.parent.mkdir(parents=True, exist_ok=True)
    run(
        "pg_dump",
        ["--format=custom", "--no-owner", "--no-acl", "--compress=6", f"--file={out_file}"],
        target,
    )


@dataclass
class DumpInfo:
    readable: bool
    archive_dbname: str | None = None
    dumped_from: str | None = None
    dumped_by: str | None = None
    created: str | None = None
    toc_entries: int = 0
    table_data: int = 0
    tables: int = 0
    indexes: int = 0
    constraints: int = 0
    sequences: int = 0
    has_schema_migrations: bool = False
    problem: str | None = None


def inspect(dump_file: Path) -> DumpInfo:
    """Read the dump's table of contents with pg_restore --list. Nothing is
    restored. A file that is not a PostgreSQL custom-format dump is refused
    before pg_restore even sees it."""
    try:
        with dump_file.open("rb") as handle:
            if handle.read(5) != PGDMP_MAGIC:
                return DumpInfo(False, problem="Not a PostgreSQL custom-format dump (.dump made by pg_dump -Fc).")
    except OSError:
        return DumpInfo(False, problem="The file cannot be read.")
    try:
        listing = run("pg_restore", ["--list", str(dump_file)], None, timeout=600)
    except PgToolError as exc:
        return DumpInfo(False, problem=str(exc))

    def header(label: str) -> str | None:
        # pg_restore writes most headers as "Label: value" but some, like
        # "Archive created at 2026-...", without the colon.
        match = re.search(rf"^;\s*{re.escape(label)}:?\s+(.+)$", listing, re.MULTILINE)
        return match.group(1).strip() if match else None

    entries = [line for line in listing.splitlines() if line and not line.startswith(";")]
    info = DumpInfo(
        True,
        archive_dbname=header("dbname"),
        dumped_from=header("Dumped from database version"),
        dumped_by=header("Dumped by pg_dump version"),
        created=header("Archive created at"),
        toc_entries=len(entries),
        table_data=sum(" TABLE DATA " in line for line in entries),
        tables=sum(re.search(r"\d+ TABLE \S+ \S+", line) is not None and " TABLE DATA " not in line for line in entries),
        indexes=sum(" INDEX " in line for line in entries),
        constraints=sum(" CONSTRAINT " in line or " FK CONSTRAINT " in line for line in entries),
        sequences=sum(" SEQUENCE " in line and " SEQUENCE SET " not in line for line in entries),
        has_schema_migrations=any("schema_migrations" in line for line in entries),
    )
    if info.table_data == 0:
        info.readable = False
        info.problem = "The dump holds no table data."
    return info


def restore(target: Target, dump_file: Path) -> None:
    """Replace the target database's objects with the dump's, as ONE
    transaction: --single-transaction means any error rolls the whole restore
    back and the database is exactly as it was. --clean --if-exists drops each
    object first; lock_timeout stops it waiting forever behind a connection
    the running portal still holds (it fails, unchanged, instead)."""
    run(
        "pg_restore",
        ["--clean", "--if-exists", "--no-owner", "--no-acl", "--single-transaction",
         "--exit-on-error", f"--dbname={target.database}", str(dump_file)],
        target,
        extra_env={"PGOPTIONS": "-c lock_timeout=30000 -c statement_timeout=0"},
    )
