"""Render the dialect-neutral SQL in app/db/sql for a concrete database.

There is one copy of the schema (app/db/sql/*.sql) and it carries typed
placeholders. Keeping a single source and rendering it beats maintaining a
SQLite DDL and a PostgreSQL DDL side by side, which drift the moment somebody
is in a hurry.

    {{UUID}}  CHAR(36)  / uuid
    {{JSON}}  TEXT      / jsonb
    {{TS}}    TIMESTAMP / timestamptz
    {{BOOL}}  BOOLEAN   / boolean
    {{NOW}}   CURRENT_TIMESTAMP / now()

A few schema changes genuinely have no shared spelling - SQLite has no ALTER
COLUMN and cannot add a table-level CHECK, so relaxing a NOT NULL means
rebuilding the table, while PostgreSQL does it in one line. Those get a
dialect block, and only the matching one survives rendering:

    {{#sqlite}}      ... only when rendering for SQLite     ... {{/sqlite}}
    {{#postgresql}}  ... only when rendering for PostgreSQL ... {{/postgresql}}

Use them sparingly: two spellings of one change is two things to keep right.
"""
from __future__ import annotations

import re
from pathlib import Path

SQL_DIR = Path(__file__).resolve().parent / "sql"

DIALECTS: dict[str, dict[str, str]] = {
    "sqlite": {
        "UUID": "CHAR(36)",
        "JSON": "TEXT",
        "TS": "TIMESTAMP",
        "BOOL": "BOOLEAN",
        "NOW": "CURRENT_TIMESTAMP",
    },
    "postgresql": {
        "UUID": "uuid",
        "JSON": "jsonb",
        "TS": "timestamptz",
        "BOOL": "boolean",
        "NOW": "now()",
    },
}

_PLACEHOLDER = re.compile(r"\{\{(\w+)\}\}")
# {{#sqlite}} ... {{/sqlite}} — the closing tag must name the same dialect,
# so a mismatched pair is a syntax error rather than a silently eaten block.
_BLOCK = re.compile(r"[ \t]*\{\{#(\w+)\}\}[ \t]*\n?(.*?)[ \t]*\{\{/\1\}\}[ \t]*\n?", re.DOTALL)


class UnknownPlaceholder(RuntimeError):
    pass


def normalise_dialect(name: str) -> str:
    """Accept the SQLAlchemy dialect names as well as the short ones."""
    name = (name or "").lower()
    if name.startswith("postgres"):
        return "postgresql"
    if name.startswith("sqlite"):
        return "sqlite"
    raise UnknownPlaceholder(
        f"Unsupported dialect {name!r}. Supported: {', '.join(DIALECTS)}"
    )


def _apply_blocks(sql: str, dialect: str) -> str:
    """Keep the block for this dialect, drop the others."""

    def sub(match: re.Match[str]) -> str:
        name, body = match.group(1), match.group(2)
        if name not in DIALECTS:
            raise UnknownPlaceholder(
                f"Unknown dialect block {{{{#{name}}}}}. "
                f"Known dialects: {', '.join(sorted(DIALECTS))}"
            )
        return f"{body}\n" if name == dialect else ""

    return _BLOCK.sub(sub, sql)


def render(sql: str, dialect: str) -> str:
    """Substitute every placeholder, or fail loudly.

    An unrecognised placeholder is a typo that would otherwise reach the
    database as literal text and produce a baffling syntax error.
    """
    dialect = normalise_dialect(dialect)
    types = DIALECTS[dialect]
    # Blocks first: a dropped block must not have its placeholders resolved,
    # and a kept one must.
    sql = _apply_blocks(sql, dialect)

    def sub(match: re.Match[str]) -> str:
        key = match.group(1)
        if key not in types:
            raise UnknownPlaceholder(
                f"Unknown SQL placeholder {{{{{key}}}}}. "
                f"Known placeholders: {', '.join(sorted(types))}"
            )
        return types[key]

    return _PLACEHOLDER.sub(sub, sql)


def migration_files() -> list[Path]:
    """Every migration, in version order. Names must sort lexicographically."""
    return sorted(SQL_DIR.glob("[0-9]*.sql"), key=lambda p: p.name)


def version_of(path: Path) -> str:
    """`0001_initial.sql` -> `0001`."""
    return path.name.split("_", 1)[0]


def split_statements(sql: str) -> list[str]:
    """Split a script into statements on top-level semicolons.

    Semicolons inside string literals and inside `--` comments do not end a
    statement. Naive `sql.split(";")` breaks the moment a DEFAULT or a CHECK
    contains one, which is exactly the kind of bug that only shows up in
    production.
    """
    statements: list[str] = []
    current: list[str] = []
    in_string = False
    in_line_comment = False
    i = 0
    while i < len(sql):
        ch = sql[i]
        nxt = sql[i + 1] if i + 1 < len(sql) else ""

        if in_line_comment:
            current.append(ch)
            if ch == "\n":
                in_line_comment = False
            i += 1
            continue

        if in_string:
            current.append(ch)
            if ch == "'":
                if nxt == "'":          # '' is an escaped quote, not a close
                    current.append(nxt)
                    i += 2
                    continue
                in_string = False
            i += 1
            continue

        if ch == "-" and nxt == "-":
            in_line_comment = True
            current.append(ch)
            i += 1
            continue

        if ch == "'":
            in_string = True
            current.append(ch)
            i += 1
            continue

        if ch == ";":
            statements.append("".join(current))
            current = []
            i += 1
            continue

        current.append(ch)
        i += 1

    statements.append("".join(current))
    return [s.strip() for s in statements if s.strip() and not _only_comments(s)]


def _only_comments(chunk: str) -> bool:
    """True when a trailing chunk is nothing but comments and whitespace."""
    for line in chunk.splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith("--"):
            return False
    return True
