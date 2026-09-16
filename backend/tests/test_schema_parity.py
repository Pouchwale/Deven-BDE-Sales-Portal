"""The SQL migrations and the ORM models must describe the same database.

The SQL in app/db/sql is the source of truth. The models mirror it. Nothing
enforces that by construction, so it is enforced here: add a column to one
and forget the other, and the build fails instead of a query failing in
production against a column that does not exist.

The second half checks the CHECK constraint vocabularies against
app/core/constants.py, so adding a lead status in Python and forgetting the
database - which would reject the new value at write time - fails here too.
"""
from __future__ import annotations

import re

import pytest
from sqlalchemy import inspect

from app.core.constants import (
    AlertStatus,
    FeedbackSource,
    ImportStatus,
    LeadOrigin,
    LeadPriority,
    LeadStatus,
    ReferenceOutcome,
    ReferenceStatus,
    Role,
)
from app.db.base import Base
from app.db.render import migration_files, render, split_statements
from app.db.session import engine

# Created by the migration runner itself, so it is not in the models.
IGNORED_TABLES = {"schema_migrations"}


def _live_tables() -> dict[str, dict]:
    inspector = inspect(engine)
    return {
        name: {c["name"]: c for c in inspector.get_columns(name)}
        for name in inspector.get_table_names()
        if name not in IGNORED_TABLES
    }


def test_every_sql_table_has_a_model() -> None:
    live = set(_live_tables())
    modelled = set(Base.metadata.tables)
    assert live == modelled, (
        f"only in the database: {sorted(live - modelled)}; "
        f"only in the models: {sorted(modelled - live)}"
    )


@pytest.mark.parametrize("table_name", sorted(Base.metadata.tables))
def test_model_columns_match_the_database(table_name: str) -> None:
    live_columns = _live_tables()[table_name]
    model_columns = {c.name: c for c in Base.metadata.tables[table_name].columns}

    assert set(live_columns) == set(model_columns), (
        f"{table_name}: only in the database {sorted(set(live_columns) - set(model_columns))}; "
        f"only in the model {sorted(set(model_columns) - set(live_columns))}"
    )

    for name, model_column in model_columns.items():
        if model_column.primary_key:
            # SQLite does not imply NOT NULL on a non-INTEGER primary key, so
            # its reflected nullability is not comparable. The PK constraint
            # itself is what matters and it is asserted below.
            continue
        assert model_column.nullable == live_columns[name]["nullable"], (
            f"{table_name}.{name}: model nullable={model_column.nullable}, "
            f"database nullable={live_columns[name]['nullable']}"
        )


@pytest.mark.parametrize("table_name", sorted(Base.metadata.tables))
def test_primary_keys_match(table_name: str) -> None:
    live_pk = set(inspect(engine).get_pk_constraint(table_name)["constrained_columns"])
    model_pk = {c.name for c in Base.metadata.tables[table_name].primary_key}
    assert live_pk == model_pk, f"{table_name}: {live_pk} vs {model_pk}"


# ------------------------------------------------- CHECK vocabularies
def _all_sql() -> str:
    return "\n".join(p.read_text(encoding="utf-8") for p in migration_files())


def _check_values(constraint_name: str) -> set[str]:
    """Pull the quoted strings out of a named CHECK constraint.

    Scans for the matching close paren rather than using a regex: a CHECK
    body may sit on one line or span several, and may contain parentheses of
    its own.

    The LAST definition wins. Migrations are concatenated in order, and a
    later one may redefine a constraint - 0006 widens ck_feedback_source to
    admit GOOGLE_FORMS_WEBHOOK. Taking the first match would test a
    constraint the database no longer has.
    """
    sql = _all_sql()
    matches = list(
        re.finditer(
            rf"CONSTRAINT\s+{constraint_name}\s+CHECK\s*\(", sql, re.IGNORECASE
        )
    )
    assert matches, f"CHECK constraint {constraint_name} not found in the SQL"
    match = matches[-1]

    depth = 1
    start = index = match.end()
    while index < len(sql) and depth:
        if sql[index] == "(":
            depth += 1
        elif sql[index] == ")":
            depth -= 1
        index += 1
    return set(re.findall(r"'([^']+)'", sql[start : index - 1]))


@pytest.mark.parametrize(
    "constraint,enum",
    [
        ("ck_users_role", Role),
        ("ck_leads_status", LeadStatus),
        ("ck_leads_priority", LeadPriority),
        ("ck_leads_origin", LeadOrigin),
        ("ck_customers_reference_status", ReferenceStatus),
        ("ck_customer_references_outcome", ReferenceOutcome),
        ("ck_feedback_source", FeedbackSource),
        ("ck_feedback_alerts_status", AlertStatus),
        ("ck_feedback_imports_status", ImportStatus),
    ],
)
def test_check_constraints_match_the_enums(constraint: str, enum: type) -> None:
    assert _check_values(constraint) == {member.value for member in enum}


# ----------------------------------------------------- the migrations
def test_migrations_render_for_both_dialects() -> None:
    """Every placeholder resolves. An unknown one raises rather than reaching
    the database as literal text."""
    for path in migration_files():
        raw = path.read_text(encoding="utf-8")
        for dialect in ("sqlite", "postgresql"):
            rendered = render(raw, dialect)
            assert "{{" not in rendered, f"{path.name} has an unresolved placeholder"
            assert split_statements(rendered)


def test_postgres_render_uses_native_types() -> None:
    """Check the statements, not the file: the header comment documents the
    SQLite spellings and would otherwise match."""
    rendered = render(migration_files()[0].read_text(encoding="utf-8"), "postgresql")
    statements = "\n".join(split_statements(rendered))
    body = "\n".join(
        line for line in statements.splitlines() if not line.strip().startswith("--")
    )
    assert "uuid" in body and "jsonb" in body and "timestamptz" in body
    assert "CHAR(36)" not in body
    assert "TIMESTAMP " not in body


def test_partial_unique_indexes_exist() -> None:
    """The two constraints that are enforced by the database rather than by a
    read-then-write race in the service layer."""
    sql = _all_sql()
    assert "ux_open_alert_per_dept" in sql and "WHERE status = 'OPEN'" in sql
    assert "ux_one_head_per_department" in sql


def test_a_table_rebuild_does_not_take_its_children_with_it(tmp_path) -> None:
    """The migration runner must survive the table-rebuild pattern.

    Changing a CHECK constraint on SQLite means create-copy-DROP-rename. With
    `PRAGMA foreign_keys=ON` - which this application sets on every connection
    - dropping a parent CASCADES INTO ITS CHILDREN. Rebuilding `leads` once
    deleted every row of `lead_activities`: the entire timeline, silently,
    with the migration reporting success.

    `migrate._migration_connection` suspends foreign keys for the duration and
    runs `foreign_key_check` before committing. This proves it, against a
    parent and child shaped like the real ones.
    """
    from sqlalchemy import create_engine, text

    from app.db import migrate

    engine = create_engine(f"sqlite:///{tmp_path / 'rebuild.db'}", future=True)

    # The application's own pragma, which is what makes this dangerous.
    from sqlalchemy import event

    @event.listens_for(engine, "connect")
    def _fk_on(dbapi_connection, _record):  # pragma: no cover - trivial
        dbapi_connection.execute("PRAGMA foreign_keys=ON")

    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE parent (id INTEGER PRIMARY KEY, name TEXT)"))
        conn.execute(
            text(
                "CREATE TABLE child ("
                "  id INTEGER PRIMARY KEY,"
                "  parent_id INTEGER REFERENCES parent(id) ON DELETE CASCADE,"
                "  note TEXT)"
            )
        )
        conn.execute(text("INSERT INTO parent (id, name) VALUES (1, 'kept')"))
        conn.execute(text("INSERT INTO child (id, parent_id, note) VALUES (1, 1, 'history')"))

    # Exactly the shape of 0010: rebuild the parent, drop the original.
    with migrate._migration_connection(engine) as conn:
        conn.execute(text("CREATE TABLE parent_rebuilt (id INTEGER PRIMARY KEY, name TEXT)"))
        conn.execute(text("INSERT INTO parent_rebuilt SELECT id, name FROM parent"))
        conn.execute(text("DROP TABLE parent"))
        conn.execute(text("ALTER TABLE parent_rebuilt RENAME TO parent"))

    with engine.connect() as conn:
        assert conn.execute(text("SELECT COUNT(*) FROM parent")).scalar() == 1
        assert conn.execute(text("SELECT COUNT(*) FROM child")).scalar() == 1, (
            "the child rows were cascaded away by the parent rebuild"
        )
    engine.dispose()
