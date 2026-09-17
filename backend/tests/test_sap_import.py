"""The SAP import, checked against the export in tests/fixtures/.

These assertions are about genuine data, not a fixture: the numbers below
were read off the file the business supplied. If a future export changes
them, this file is where that shows up.
"""
from __future__ import annotations

import csv
from datetime import date

import pytest
from sqlalchemy import func, select

from app.core.config import settings
from app.core.constants import ReferenceStatus
from app.models.customer import Customer, InvoiceLine
from app.models.org import User
from app.services.customer_source import (
    FileCustomerSource,
    UnmappedColumns,
    normalise_header,
    parse_date,
)
from app.services.sap_import import import_source, row_hash

SAP_FILE = settings.sap_data_files[0]

# Owner -> number of distinct customers, straight out of the export's own
# Sales Person column. Matches plan v3 s13, which omits only Bhakti Shah's 1.
EXPECTED_OWNERSHIP = {
    "Apurva Shah": 4,
    "Shailesh Prajapati": 3,
    "Aastha Ramchandani": 2,
    "Navya Rupawat": 2,
    "Bhakti Shah": 1,
    "Nidhi Ratnakar": 1,
    "Parag Sharma": 1,
    "Parth Fulvani": 1,
    "Sanjeev Singh": 1,
    "Urvish Dave": 1,
}


def test_the_real_export_is_present() -> None:
    assert SAP_FILE.exists(), f"the real SAP export is missing from {SAP_FILE}"


# ------------------------------------------------------------ the parser
def test_headers_normalise() -> None:
    assert normalise_header("  Invoice   No ") == "invoice no"
    assert normalise_header("﻿Customer") == "customer"


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("2026-06-27", date(2026, 6, 27)),
        ("27-06-2026", date(2026, 6, 27)),
        ("27/06/2026", date(2026, 6, 27)),
        ("", None),
        ("not a date", None),
    ],
)
def test_dates_are_read_day_first(raw: str, expected: date | None) -> None:
    """The export is Indian. A month-first reading would move June to
    December and nothing downstream would notice."""
    assert parse_date(raw) == expected


def test_missing_columns_are_reported_not_guessed(tmp_path) -> None:
    bad = tmp_path / "bad.csv"
    bad.write_text("Customer,Mobile\nC1,999\n", encoding="utf-8")
    with pytest.raises(UnmappedColumns) as excinfo:
        list(FileCustomerSource(bad).rows())
    assert "name" in excinfo.value.missing and "invoice_no" in excinfo.value.missing


def test_source_reads_every_line_of_the_real_file() -> None:
    rows = list(FileCustomerSource(SAP_FILE).rows())
    with SAP_FILE.open(encoding="utf-8-sig", newline="") as handle:
        raw = list(csv.DictReader(handle))
    assert len(rows) == len(raw) == 30


def test_row_hashes_are_unique_across_the_real_file() -> None:
    """Two lines differing only by invoice number must not collide - the real
    file has exactly that case (invoices 1375 and 1376)."""
    rows = list(FileCustomerSource(SAP_FILE).rows())
    assert len({row_hash(r) for r in rows}) == len(rows) == 30


# ---------------------------------------------------------- the seeded data
def test_seed_loaded_the_real_customers(db) -> None:
    assert db.scalar(select(func.count(Customer.id))) == 17
    assert db.scalar(select(func.count(InvoiceLine.id))) == 30
    # 23 distinct invoice numbers across 30 lines: invoices carry several items.
    assert db.scalar(select(func.count(func.distinct(InvoiceLine.invoice_no)))) == 23


def test_customer_values_are_stored_exactly_as_sap_supplied_them(db) -> None:
    """Nothing is cleaned, reformatted or title-cased on the way in.

    The mobile is the one deliberate exception. SAP exports the same number
    three ways - `+91 97111 22505`, `091-9711122505`, bare ten digits - and
    keeping them verbatim meant two records of one person did not compare
    equal, and a 10-digit rule could not be applied without rejecting real
    customers. It is normalised to the ten digits and nothing else; the
    digits are untouched.
    """
    guiltfree = db.execute(
        select(Customer).where(Customer.sap_code == "C1730")
    ).scalar_one()
    assert guiltfree.name == "GUILTFREE INDUSTRIES LIMITED"
    assert guiltfree.mobile == "9711122505", "+91 and spaces stripped, digits kept"
    assert guiltfree.email == "ronak.parmar@rpsg.in"

    jagdamba = db.execute(
        select(Customer).where(Customer.sap_code == "C1282")
    ).scalar_one()
    assert jagdamba.name == "Jagdamba dryfruits"          # not upper-cased
    assert jagdamba.email == "Nottynutssales@gmail.com"   # capital N preserved


def test_ownership_comes_from_the_files_own_sales_person_column(db) -> None:
    rows = db.execute(
        select(User.name, func.count(Customer.id))
        .join(Customer, Customer.owner_user_id == User.id)
        .group_by(User.name)
    ).all()
    assert dict(rows) == EXPECTED_OWNERSHIP


def test_every_sales_person_resolved_to_an_account(db) -> None:
    """No customer is left unowned: all ten names in the export are staff."""
    unowned = db.scalar(
        select(func.count(Customer.id)).where(Customer.owner_user_id.is_(None))
    )
    assert unowned == 0


def test_invoice_rollups(db) -> None:
    high_grade = db.execute(
        select(Customer).where(Customer.sap_code == "C2262")
    ).scalar_one()
    # Three lines, all on invoice 1398 - that is one invoice, not three.
    assert len(high_grade.invoice_lines) == 3
    assert high_grade.invoice_count == 1
    assert high_grade.first_invoice_date == date(2026, 6, 29)
    assert high_grade.last_invoice_date == date(2026, 6, 29)

    sweet_karam = db.execute(
        select(Customer).where(Customer.sap_code == "C2077")
    ).scalar_one()
    assert len(sweet_karam.invoice_lines) == 4
    assert sweet_karam.invoice_count == 4        # 1375, 1376, 1377, 1431
    assert sweet_karam.first_invoice_date == date(2026, 6, 27)
    assert sweet_karam.last_invoice_date == date(2026, 7, 1)


def test_every_customer_starts_unasked_for_a_reference(db) -> None:
    statuses = db.execute(select(Customer.reference_status).distinct()).scalars().all()
    assert set(statuses) == {ReferenceStatus.NOT_ASKED}


def test_no_transactional_data_was_invented(db) -> None:
    """The seed creates no leads, references or feedback. Everything in those
    tables comes from real use or a real import."""
    from app.models.feedback import Feedback
    from app.models.lead import Lead
    from app.models.reference import CustomerReference

    assert db.scalar(select(func.count(Lead.id))) == 0
    assert db.scalar(select(func.count(CustomerReference.id))) == 0
    assert db.scalar(select(func.count(Feedback.id))) == 0


# ------------------------------------------------------------ idempotency
def test_reimporting_the_same_export_creates_nothing(db) -> None:
    result = import_source(db, FileCustomerSource(SAP_FILE))
    assert result.total_rows == 30
    assert result.lines_created == 0
    assert result.lines_skipped == 30
    assert result.customers_created == 0
    assert result.error_count == 0
    assert db.scalar(select(func.count(Customer.id))) == 17


def test_a_reassigned_owner_survives_the_next_export(db) -> None:
    """A portal reassignment is a deliberate management act. Re-importing the
    same file must not quietly hand the account back to the SAP salesperson."""
    customer = db.execute(
        select(Customer).where(Customer.sap_code == "C2243")
    ).scalar_one()
    kevin = db.execute(select(User).where(User.name == "Kevin")).scalar_one()
    original_owner = customer.owner_user_id
    customer.owner_user_id = kevin.id
    db.flush()

    import_source(db, FileCustomerSource(SAP_FILE))
    db.refresh(customer)
    assert customer.owner_user_id == kevin.id != original_owner
    # The SAP name is still recorded, so the divergence is visible.
    assert customer.sap_sales_person == "Apurva Shah"


def test_a_new_line_in_a_later_export_is_picked_up(db, tmp_path) -> None:
    extra = tmp_path / "sap_extra.csv"
    with SAP_FILE.open(encoding="utf-8-sig") as handle:
        content = handle.read().rstrip("\n")
    content += (
        "\nC2243,JOVAKI AGRO FOOD INDIA PRIVATE LIMITED,9257108463,"
        "jovakioperations@gmail.com,FGPO9999,\"A Later Item (F+B) Pouch\","
        "2026-07-10,1999,Apurva Shah\n"
    )
    extra.write_text(content, encoding="utf-8")

    result = import_source(db, FileCustomerSource(extra))
    assert result.lines_created == 1
    assert result.lines_skipped == 30
    assert result.customers_created == 0

    customer = db.execute(
        select(Customer).where(Customer.sap_code == "C2243")
    ).scalar_one()
    assert customer.last_invoice_date == date(2026, 7, 10)


def test_an_unknown_sales_person_is_reported_and_left_unowned(db, tmp_path) -> None:
    unknown = tmp_path / "sap_unknown.csv"
    unknown.write_text(
        "Customer,Customer Name,Mobile,Email,FGPO Code,Item Description,"
        "Invoice Date,Invoice No,Sales Person\n"
        "C9999,A NEW ACCOUNT PVT LTD,9000000000,new@example.com,FGPO0001,"
        "\"Something (F+B) Pouch\",2026-07-15,2001,Someone Not On Staff\n",
        encoding="utf-8",
    )
    result = import_source(db, FileCustomerSource(unknown))
    assert result.unmatched_sales_people == {"Someone Not On Staff"}

    customer = db.execute(
        select(Customer).where(Customer.sap_code == "C9999")
    ).scalar_one()
    assert customer.owner_user_id is None
    assert customer.sap_sales_person == "Someone Not On Staff"


def test_a_row_with_no_customer_code_is_an_error_not_a_crash(db, tmp_path) -> None:
    broken = tmp_path / "sap_broken.csv"
    broken.write_text(
        "Customer,Customer Name,Mobile,Email,FGPO Code,Item Description,"
        "Invoice Date,Invoice No,Sales Person\n"
        ",NO CODE LTD,9000000000,x@example.com,FGPO0002,\"Item\",2026-07-15,2002,Kevin\n",
        encoding="utf-8",
    )
    result = import_source(db, FileCustomerSource(broken))
    assert result.error_count == 1
    assert result.lines_created == 0
    assert "no customer code" in result.errors[0]["message"]


# ------------------------------------------------------------ data integrity
def _counts(db) -> dict[str, int]:
    from app.models.lead import Lead, LeadActivity
    from app.models.post_sale import PostSaleRecord

    return {
        "customers": db.scalar(select(func.count(Customer.id))),
        "lines": db.scalar(select(func.count(InvoiceLine.id))),
        "leads": db.scalar(select(func.count(Lead.id))),
        "activities": db.scalar(select(func.count(LeadActivity.id))),
        "post_sale": db.scalar(select(func.count(PostSaleRecord.id))),
    }


INTEGRITY_HEADER = (
    "Customer Code,Customer Name,Mobile,Email,FGPO Code,Invoice Date,Sales Person\n"
)


def test_importing_the_same_file_twice_creates_nothing_the_second_time(db, tmp_path) -> None:
    export = tmp_path / "twice.csv"
    export.write_text(
        INTEGRITY_HEADER
        + "C8101,TWICE FOODS LLP,9000000101,twice@example.com,FGPO8101,2026-09-01,Kevin\n"
        + "C8101,TWICE FOODS LLP,9000000101,twice@example.com,FGPO8102,2026-09-03,Kevin\n"
        + "C8102,ONCE MORE LLP,9000000102,,FGPO8103,2026-09-02,Apurva Shah\n",
        encoding="utf-8",
    )
    before = _counts(db)
    first = import_source(db, FileCustomerSource(export), link_leads=True)
    after_first = _counts(db)
    assert first.customers_created == 2 and first.lines_created == 3
    assert after_first["customers"] == before["customers"] + 2
    assert after_first["leads"] == before["leads"] + 2
    assert after_first["post_sale"] == before["post_sale"] + 2

    second = import_source(db, FileCustomerSource(export), link_leads=True)
    assert second.customers_created == 0
    assert second.lines_created == 0 and second.lines_skipped == 3
    assert second.leads_created == 0 and second.owners_changed == 0
    assert _counts(db) == after_first


def test_a_duplicated_row_inside_one_file_is_stored_once(db, tmp_path) -> None:
    row = "C8201,DUPLICATE ROW LLP,9000000201,,FGPO8201,2026-09-01,Kevin\n"
    export = tmp_path / "dupes.csv"
    export.write_text(INTEGRITY_HEADER + row + row + row, encoding="utf-8")
    before = _counts(db)
    result = import_source(db, FileCustomerSource(export), link_leads=True)
    assert result.total_rows == 3
    assert result.lines_created == 1 and result.lines_skipped == 2
    after = _counts(db)
    assert after["customers"] == before["customers"] + 1
    assert after["lines"] == before["lines"] + 1
    assert after["leads"] == before["leads"] + 1


def test_a_failure_mid_import_leaves_nothing_behind(db, tmp_path, monkeypatch) -> None:
    from app.services import sap_import, sap_sync

    export = tmp_path / "half.csv"
    export.write_text(
        INTEGRITY_HEADER
        + "C8301,HALF ONE LLP,9000000301,,FGPO8301,2026-09-01,Kevin\n"
        + "C8302,HALF TWO LLP,9000000302,,FGPO8302,2026-09-01,Kevin\n",
        encoding="utf-8",
    )
    before = _counts(db)

    def explode(*args, **kwargs):
        raise RuntimeError("database went away half-way")

    # Customers and lines are already flushed when lead linking fails.
    monkeypatch.setattr(sap_import, "link_customer_leads", explode)
    monkeypatch.setattr(sap_import.settings, "SAP_LINK_LEADS", True)
    with pytest.raises(RuntimeError):
        sap_sync.run_import(db, export)
    db.expire_all()
    assert _counts(db) == before
    assert db.execute(select(Customer).where(Customer.sap_code == "C8301")).first() is None


def test_a_sales_person_matching_only_a_deactivated_account_is_unmatched(
    db, tmp_path
) -> None:
    kevin = db.execute(select(User).where(User.name == "Kevin")).scalar_one()
    kevin.is_active = False
    db.flush()

    export = tmp_path / "inactive.csv"
    export.write_text(
        INTEGRITY_HEADER + "C8401,INACTIVE OWNER LLP,9000000401,,FGPO8401,2026-09-01,Kevin\n",
        encoding="utf-8",
    )
    result = import_source(db, FileCustomerSource(export), link_leads=True)
    assert result.unmatched_sales_people == {"Kevin"}
    customer = db.execute(select(Customer).where(Customer.sap_code == "C8401")).scalar_one()
    assert customer.owner_user_id is None
    assert customer.sap_sales_person == "Kevin"
