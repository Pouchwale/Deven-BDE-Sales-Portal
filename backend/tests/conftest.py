"""Test fixtures.

The database is built by running the real migrations against a throwaway
file, then running the real seed. Nothing here builds a schema of its own, so
a migration that is wrong fails the whole suite immediately rather than
letting the tests pass against a shape production will never have.

Set TEST_DATABASE_URL to run the identical suite against PostgreSQL:

    TEST_DATABASE_URL=postgresql+psycopg2://user:pass@localhost/bde_test pytest
"""
from __future__ import annotations

import os
import tempfile
import uuid
from pathlib import Path

import pytest

# Must happen before anything imports app.core.config, which reads the
# environment once at import time. Environment beats the .env file.
_TMP_DIR = Path(tempfile.mkdtemp(prefix="bde_portal_test_"))
os.environ.setdefault("TEST_DATABASE_URL", f"sqlite:///{_TMP_DIR / 'test.db'}")
os.environ["DATABASE_URL"] = os.environ["TEST_DATABASE_URL"]
os.environ["SEED_PASSWORD"] = "TestPass@123"
os.environ["SECRET_KEY"] = "test-secret-key-not-used-anywhere-real"
# Pinned ON regardless of the developer's .env: the forced password change is
# a behaviour worth keeping under test even while it is switched off for
# convenience during development.
os.environ["SEED_FORCE_PASSWORD_CHANGE"] = "true"
# The suite's numbers are written against this fixed export, never against the
# live SAP workbook the portal itself imports.
os.environ["SAP_DATA_FILE"] = str(Path(__file__).parent / "fixtures" / "sap_invoices_real.csv")
# The suite's lead and reference counts assume the seed creates no leads.
# test_admin_sap_import.py covers the SAP -> lead link explicitly.
os.environ["SAP_LINK_LEADS"] = "false"
os.environ["SAP_AUTO_SYNC"] = "false"

from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import select, text  # noqa: E402
from sqlalchemy.orm import Session, object_session  # noqa: E402

import app.models  # noqa: E402,F401  (registers every mapper)
from app.core.config import settings  # noqa: E402
from app.db import migrate  # noqa: E402
from app.db.session import engine, get_db  # noqa: E402
from app.main import app as fastapi_app  # noqa: E402
from app.models.org import User  # noqa: E402
from app.seeds import seed as seed_module  # noqa: E402

SEED_PASSWORD = "TestPass@123"


#: Filled in by `_database`. The seed's Super Admin, whoever that turns out
#: to be - read from the database rather than hard-coded, so renaming the
#: seeded owner does not quietly break thirty tests.
SUPER_ADMIN_EMAIL: str = ""


@pytest.fixture(scope="session", autouse=True)
def _database() -> None:
    """One migrated, seeded database for the whole run."""
    if engine.dialect.name == "postgresql":
        # A pre-existing schema would make the migrations fail on a rerun.
        with engine.begin() as conn:
            conn.execute(text("DROP SCHEMA public CASCADE; CREATE SCHEMA public;"))
    migrate.upgrade(engine, verbose=False)
    seed_module.run()

    # The Super Admin is signed in by `super_admin_headers` from inside tests
    # that are already holding the database in a transaction, so it cannot
    # open a second session to clear the forced password change the way
    # `sign_in` does - SQLite would deadlock against the test's own write
    # lock. Clearing it once, here, where nothing else is holding the file.
    global SUPER_ADMIN_EMAIL
    from app.core.constants import Role

    with Session(engine) as session:
        owner = (
            session.execute(select(User).where(User.role == Role.SUPER_ADMIN))
            .scalars()
            .first()
        )
        assert owner is not None, "the seed must create a Super Admin"
        SUPER_ADMIN_EMAIL = owner.email
        owner.must_change_password = False
        session.commit()

    yield
    engine.dispose()


@pytest.fixture
def db() -> Session:
    """A session whose writes are rolled back at the end of the test.

    The outer transaction never commits; `create_savepoint` turns the
    application's own commit() calls into savepoint releases inside it, so
    committed code paths are exercised without leaking between tests.
    """
    connection = engine.connect()
    transaction = connection.begin()
    # expire_on_commit=False mirrors SessionLocal. The default (True) hides a
    # whole class of bug: an object re-read after commit would look fresh in
    # tests and come back stale from the identity map in the real app.
    session = Session(
        bind=connection,
        join_transaction_mode="create_savepoint",
        expire_on_commit=False,
    )
    try:
        yield session
    finally:
        session.close()
        transaction.rollback()
        connection.close()


@pytest.fixture
def client(db: Session) -> TestClient:
    """A TestClient sharing the test's session, so API writes roll back too."""
    fastapi_app.dependency_overrides[get_db] = lambda: db
    from app.api.auth import login_limiter
    from app.api.chat import message_limiter

    # Both are module-level and per-process, so without this a test fails
    # because of how many requests the tests before it happened to make.
    login_limiter.clear()
    message_limiter.clear()
    with TestClient(fastapi_app) as test_client:
        yield test_client
    fastapi_app.dependency_overrides.clear()


# ----------------------------------------------------------- lookup helpers
@pytest.fixture
def users(db: Session) -> dict[str, User]:
    """Every seeded user, keyed by name - the org chart reads better in a
    test than a list of UUIDs does."""
    return {u.name: u for u in db.query(User).all()}


def token_for(client: TestClient, email: str, password: str = SEED_PASSWORD) -> str:
    response = client.post("/api/auth/login", json={"email": email, "password": password})
    assert response.status_code == 200, response.text
    return response.json()["access_token"]


def auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def sign_in(client: TestClient, user: User, password: str = SEED_PASSWORD) -> dict[str, str]:
    """Headers for a seeded user, with the forced password change cleared.

    Every seeded account starts with must_change_password set, which blocks
    the rest of the API by design. Tests that are not about that flag clear
    it rather than performing a password change in every single one.

    Committed, not merely flushed: a real password change commits, and a
    flush would be undone by any request that rolls back - which would then
    fail the *next* call with PASSWORD_CHANGE_REQUIRED for no reason the
    test could see. The outer transaction still rolls the whole thing back
    when the test ends.
    """
    if user.must_change_password:
        user.must_change_password = False
        session = object_session(user)
        if session is not None:
            session.commit()

    return auth(token_for(client, user.email, password))


def super_admin_headers(client: TestClient) -> dict[str, str]:
    """Headers for the Super Admin.

    Exists because browsing the customer book became Super Admin only, and a
    good many tests need to LOOK UP a customer in order to test something else
    entirely. No database session of its own - see `_database`, which clears
    this account's forced password change once so this can be a plain login.
    """
    assert SUPER_ADMIN_EMAIL, "_database must have run"
    return auth(token_for(client, SUPER_ADMIN_EMAIL))


#: The legal route from a brand new lead to a won one, in order. No stage can
#: be skipped - that is the point of the pipeline - so tests that just want a
#: converted lead walk it rather than jumping to the end.
LEAD_PATH_TO_CONVERTED: tuple[str, ...] = (
    "CONTACTED",
    "NURTURING",
    "PRE_QUALIFIED",
    "QUALIFIED",
    "CONVERTED",
)


def advance_lead(client, headers, lead_id, *, to: str = "CONVERTED") -> dict:
    """Move a lead up the pipeline to `to`, one legal stage at a time.

    Every move carries a remark, because every move requires one.
    """
    lead: dict = {}
    for step in LEAD_PATH_TO_CONVERTED:
        response = client.post(
            f"/api/leads/{lead_id}/status",
            headers=headers,
            json={"status": step, "remark": f"Test fixture: moved to {step}."},
        )
        assert response.status_code == 200, f"{step}: {response.text}"
        lead = response.json()
        if step == to:
            break
    return lead


def eligible_lead(client, users, *, assignee: str = "Parth Fulvani", name: str = "Askable Co") -> dict:
    """A lead that is genuinely askable: assigned, won, and synced.

    Three steps, because all three are real preconditions now:

      1. a manager assigns it
      2. the assignee walks it to CONVERTED
      3. the post-sale sheet says it was invoiced, 30 days ago

    Only then does it appear in Reference Tracking or the feedback queue.
    Skipping step 3 in a test is how you end up asserting against an empty
    module and calling it a bug.
    """
    manager = sign_in(client, users["Navya Rupawat"])
    lead = client.post(
        "/api/leads",
        headers=manager,
        json={
            "name": name,
            "mobile": "9800000111",
            "email": f"{name.replace(' ', '').lower()}@example.com",
            "assigned_to_user_id": str(users[assignee].id),
        },
    ).json()
    advance_lead(client, manager, lead["id"])
    sync_post_sale(client, sign_in(client, users["Shail Patel"]), lead=lead)
    return client.get(f"/api/leads/{lead['id']}", headers=manager).json()


def sync_post_sale(client, headers, *, lead: dict, invoiced_days_ago: int = 30) -> dict:
    """Put one lead through the post-sale sync, as the sheet would.

    Reference and feedback work is measured against leads the external sheet
    says were invoiced - so a test that wants an askable account has to sync
    one, exactly as production will. Matching is by mobile, which is the
    strongest key the seeded leads carry.
    """
    from datetime import date, timedelta

    invoiced = (date.today() - timedelta(days=invoiced_days_ago)).isoformat()
    response = client.post(
        "/api/post-sale/sync",
        headers=headers,
        json={
            "rows": [
                {
                    "external_ref": f"SHEET-{lead['id'][:8]}",
                    "customer_name": lead["name"],
                    "company_name": lead.get("company_name"),
                    "mobile": lead.get("mobile"),
                    "email": lead.get("email"),
                    "invoice_date": invoiced,
                }
            ]
        },
    )
    assert response.status_code == 200, response.text
    return response.json()


def new_email() -> str:
    return f"test-{uuid.uuid4().hex[:10]}@example.com"


__all__ = ["SEED_PASSWORD", "auth", "new_email", "sign_in", "token_for"]
