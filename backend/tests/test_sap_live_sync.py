"""The live SAP workbook: every edit made in the file must reach the portal
exactly, and a bad edit must never damage a good record."""
from __future__ import annotations

import contextlib
import os
import time
from datetime import date, timedelta
from pathlib import Path

import pytest
from sqlalchemy import select

from app.models.customer import Customer, InvoiceLine
from app.models.lead import Lead
from app.models.org import User
from app.models.post_sale import PostSaleRecord
from app.services import metrics, sap_sync
from app.services.customer_source import FileCustomerSource
from app.services.sap_import import import_source

HEADER = "Customer Code,Customer Name,Mobile,Email,FGPO Code,Invoice Date,Sales Person\n"
REF_HEADER = (
    "Customer Code,Customer Name,Mobile,Email,FGPO Code,Invoice Date,"
    "Reference Date,Sales Person\n"
)


def write(path: Path, *rows: str) -> Path:
    path.write_text(HEADER + "".join(row + "\n" for row in rows), encoding="utf-8")
    return path


def run(db, path: Path):
    return import_source(db, FileCustomerSource(path), link_leads=True)


def customer(db, code: str) -> Customer:
    db.expire_all()
    return db.execute(select(Customer).where(Customer.sap_code == code)).scalar_one()


def lead_for(db, code: str) -> tuple[Lead, PostSaleRecord]:
    record = db.execute(
        select(PostSaleRecord).where(PostSaleRecord.external_ref == f"SAP:{code}")
    ).scalar_one()
    return db.get(Lead, record.lead_id), record


def user(db, name: str) -> User:
    return db.execute(select(User).where(User.name == name)).scalar_one()


ROW = "C9001,LIVE FOODS LLP,9000000001,live@example.com,FGPO9001,2026-03-01,Kevin"


def test_a_sales_person_changed_in_the_file_reassigns_customer_and_lead(db, tmp_path) -> None:
    path = write(tmp_path / "sap.csv", ROW)
    run(db, path)

    write(path, ROW.replace(",Kevin", ",Apurva Shah"))
    result = run(db, path)

    apurva = user(db, "Apurva Shah")
    assert result.owners_changed == 1
    assert customer(db, "C9001").owner_user_id == apurva.id
    lead, _ = lead_for(db, "C9001")
    assert lead.assigned_to_user_id == apurva.id
    assert "reassigned from Kevin to Apurva Shah" in lead.activities[-1].remark


def test_a_portal_reassignment_survives_while_the_file_is_unchanged(db, tmp_path) -> None:
    path = write(tmp_path / "sap.csv", ROW)
    run(db, path)
    apurva = user(db, "Apurva Shah")
    lead, _ = lead_for(db, "C9001")
    lead.assigned_to_user_id = apurva.id
    db.flush()
    customer(db, "C9001").owner_user_id = apurva.id
    db.flush()

    run(db, path)
    assert customer(db, "C9001").owner_user_id == apurva.id
    assert lead_for(db, "C9001")[0].assigned_to_user_id == apurva.id


def test_a_newer_invoice_is_added_and_history_and_clock_are_kept(db, tmp_path) -> None:
    """The query now shows the customer's latest invoice instead of the first:
    that is a new invoice, not a correction."""
    path = write(tmp_path / "sap.csv", ROW)
    run(db, path)

    write(path, ROW.replace("FGPO9001,2026-03-01", "FGPO9002,2026-09-10"))
    result = run(db, path)

    assert result.lines_created == 1
    account = customer(db, "C9001")
    lines = db.execute(
        select(InvoiceLine).where(InvoiceLine.customer_id == account.id)
    ).scalars().all()
    assert sorted((line.fgpo_code, line.invoice_date) for line in lines) == [
        ("FGPO9001", date(2026, 3, 1)),
        ("FGPO9002", date(2026, 9, 10)),
    ]
    assert account.first_invoice_date == date(2026, 3, 1)
    assert account.last_invoice_date == date(2026, 9, 10)
    assert account.invoice_count == 2
    # Eligibility still runs from the first invoice.
    assert lead_for(db, "C9001")[1].invoice_date == date(2026, 3, 1)


def test_contact_details_follow_the_file_including_a_cleared_email(db, tmp_path) -> None:
    path = write(tmp_path / "sap.csv", ROW)
    run(db, path)

    write(path, "C9001,LIVE FOODS PVT LTD,9000000009,,FGPO9001,2026-03-01,Kevin")
    run(db, path)

    account = customer(db, "C9001")
    assert (account.name, account.mobile, account.email) == (
        "LIVE FOODS PVT LTD", "9000000009", None
    )
    lead, _ = lead_for(db, "C9001")
    assert (lead.name, lead.mobile, lead.email) == ("LIVE FOODS PVT LTD", "9000000009", None)


def test_a_row_with_a_bad_date_leaves_the_customer_untouched(db, tmp_path) -> None:
    path = write(tmp_path / "sap.csv", ROW)
    run(db, path)

    write(path, "C9001,TYPO MID EDIT,9000000001,live@example.com,FGPO9001,31-31-2026,Nobody")
    result = run(db, path)

    assert result.error_count == 1
    account = customer(db, "C9001")
    assert account.name == "LIVE FOODS LLP"
    assert account.first_invoice_date == date(2026, 3, 1)
    assert account.owner_user_id == user(db, "Kevin").id


def test_an_unknown_sales_person_never_unassigns_anybody(db, tmp_path) -> None:
    path = write(tmp_path / "sap.csv", ROW)
    run(db, path)

    write(path, ROW.replace(",Kevin", ",Kevin Typo"))
    result = run(db, path)

    assert result.unmatched_sales_people == {"Kevin Typo"}
    assert customer(db, "C9001").owner_user_id == user(db, "Kevin").id


def test_a_customer_missing_from_the_file_is_reported_not_deleted(db, tmp_path) -> None:
    other = "C9002,SECOND ACCOUNT,9000000002,second@example.com,FGPO9003,2026-03-02,Kevin"
    path = write(tmp_path / "sap.csv", ROW, other)
    run(db, path)

    write(path, ROW)
    result = run(db, path)

    assert "C9002" in result.not_in_file
    assert customer(db, "C9002").name == "SECOND ACCOUNT"


def test_the_watcher_imports_a_new_save_and_ignores_an_unchanged_file(
    db, tmp_path, monkeypatch
) -> None:
    path = write(tmp_path / "live.csv", ROW)
    old = time.time() - 60
    os.utime(path, (old, old))

    monkeypatch.setattr(sap_sync.settings, "SAP_DATA_FILE", str(path))
    monkeypatch.setattr(sap_sync.settings, "SAP_LINK_LEADS", True)
    monkeypatch.setattr(sap_sync, "SessionLocal", lambda: contextlib.nullcontext(db))
    sap_sync.forget_linked_signature()

    assert sap_sync.check_once() is True
    assert customer(db, "C9001").name == "LIVE FOODS LLP"
    assert sap_sync.check_once() is False          # nothing saved since
    assert sap_sync.status()["in_sync"] is True

    write(path, ROW.replace("LIVE FOODS LLP", "LIVE FOODS RENAMED"))
    os.utime(path, (old + 10, old + 10))
    assert sap_sync.check_once() is True
    assert customer(db, "C9001").name == "LIVE FOODS RENAMED"


def test_the_watcher_changes_nothing_when_the_file_cannot_be_read(
    db, tmp_path, monkeypatch
) -> None:
    path = tmp_path / "live.xlsx"
    path.write_bytes(b"not really a workbook - a save caught half-way")
    old = time.time() - 60
    os.utime(path, (old, old))
    monkeypatch.setattr(sap_sync.settings, "SAP_DATA_FILE", str(path))
    monkeypatch.setattr(sap_sync, "SessionLocal", lambda: contextlib.nullcontext(db))
    sap_sync.forget_linked_signature()

    before = db.execute(select(Customer.id)).scalars().all()
    assert sap_sync.check_once() is False
    assert sap_sync.status()["last_error"]
    assert db.execute(select(Customer.id)).scalars().all() == before


def test_the_sheets_reference_date_decides_when_an_account_may_be_asked(
    db, tmp_path
) -> None:
    """The workbook publishes Reference Date, so the portal reads it instead of
    recomputing invoice + 10 days. Here the sheet says 40 days, not 10."""
    path = tmp_path / "sap.csv"
    invoiced = date.today() - timedelta(days=20)
    path.write_text(
        REF_HEADER
        + f"C9101,SLOW REFERENCE LLP,9000000101,slow@example.com,FGPO9101,"
        f"{invoiced:%Y-%m-%d},{invoiced + timedelta(days=40):%Y-%m-%d},Kevin\n"
        + f"C9102,DUE TODAY LLP,9000000102,due@example.com,FGPO9102,"
        f"{invoiced:%Y-%m-%d},{date.today():%Y-%m-%d},Kevin\n",
        encoding="utf-8",
    )
    run(db, path)

    slow, _ = lead_for(db, "C9101")
    due, _ = lead_for(db, "C9102")
    ready = metrics.reference_ready_dates(db, [slow.id, due.id])
    assert ready[slow.id] == invoiced + timedelta(days=40)
    assert ready[due.id] == date.today()

    # Invoiced 20 days ago, so the old rule would have made both askable.
    eligible = metrics.post_sale_population(db, {u.id for u in [user(db, "Kevin")]})
    assert due.id in eligible["eligible_lead_ids"]
    assert slow.id not in eligible["eligible_lead_ids"]


def test_a_lead_with_no_reference_date_still_uses_invoice_plus_ten(db, tmp_path) -> None:
    """Anything not from the workbook keeps the old rule."""
    path = write(tmp_path / "sap.csv", ROW)      # no Reference Date column
    run(db, path)

    lead, record = lead_for(db, "C9001")
    assert record.reference_date is None
    assert metrics.reference_ready_dates(db, [lead.id])[lead.id] == date(2026, 3, 11)


@pytest.fixture(autouse=True)
def _reset_sync_state():
    yield
    sap_sync.forget_linked_signature()
    sap_sync._state.update(last_error=None, last_result=None)
