"""Ingest SAP invoice lines into customers and invoice_lines.

The SAP workbook is a Power Query over SAP, refreshed on a schedule. So an
import brings each customer IN THE FILE up to date, and never guesses:

  * Idempotent. Every line carries a sha256 of its normalised source row;
    re-running the same export changes nothing.
  * The file wins for the account's current details. Name, mobile, email and
    Sales Person are set to what the file says now; a cleared email is
    cleared.
  * Invoices accumulate. A new FGPO or date for a customer is a new invoice
    line; earlier lines are kept, so a query that now shows a later invoice
    (or a different date window) cannot erase history or restart the
    ten-day reference clock, which runs from the FIRST invoice.
  * A Sales Person changed IN THE FILE reassigns the account. A reassignment
    made in the portal survives every import that leaves the file's Sales
    Person as it was - whichever change is newer wins.
  * A bad row changes nothing. A row with no code, name or readable invoice
    date is rejected and that customer is left exactly as it was, so a typo
    mid-edit cannot wipe a good record.
  * Nothing is deleted. A customer missing from the file (a filtered or
    half-edited sheet looks exactly like that) stays in the portal and is
    reported in `not_in_file`.

Ownership comes from the export's own Sales Person column, matched against
portal accounts by name. An unmatched salesperson never unassigns anybody: a
new account is left unowned, an existing one keeps its owner, and the SAP
spelling is reported instead of being silently dropped.
"""
from __future__ import annotations

import hashlib
import re
import uuid
from dataclasses import dataclass, field
from typing import Iterable

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core import text, validators
from app.core.config import settings
from app.core.constants import (
    AuditAction,
    EntityType,
    ImportStatus,
    LeadActivityType,
    LeadOrigin,
    LeadStatus,
    SapImportSource,
)
from app.db.base import utcnow
from app.models.customer import Customer, InvoiceLine, SapImport
from app.models.lead import Lead, LeadActivity
from app.models.org import User
from app.models.post_sale import PostSaleRecord
from app.services import audit
from app.services.customer_source import COLUMN_MAP, CustomerRow, CustomerSource

_WHITESPACE = re.compile(r"\s+")


def row_hash(row: CustomerRow) -> str:
    """Identity of one invoice line.

    Built from the mapped business fields rather than the whole raw row, so
    an extra column appearing in a later export does not make every existing
    line look new. Invoice number alone is not enough - one invoice
    legitimately has several lines.
    """
    parts = [
        row.sap_code,
        row.invoice_no,
        row.fgpo_code or "",
        row.item_description or "",
        row.invoice_date.isoformat() if row.invoice_date else "",
    ]
    joined = "|".join(_WHITESPACE.sub(" ", p.strip()).lower() for p in parts)
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()


#: Name comparison key. Shared with the post-sale matcher - app/core/text.py.
_match_key = text.match_key


def _clean_mobile(value: str | None) -> tuple[str | None, bool]:
    """(number, was_dropped). Normalises what it can, refuses to invent.

    SAP is the system of record for the ACCOUNT, not for the phone number. A
    customer whose mobile is malformed is still a real customer with real
    invoices, so the row is imported and the number is dropped - rejecting the
    whole row would lose the account over a typo in a field nothing depends on.
    The drop is reported, so it is visible rather than silent.
    """
    try:
        return validators.normalise_phone(value), False
    except validators.InvalidPhone:
        return None, True


@dataclass
class ImportResult:
    filename: str
    total_rows: int = 0
    customers_created: int = 0
    customers_updated: int = 0
    #: Rows whose mobile could not be read as a 10-digit number. The customer
    #: was still imported; only the number was left blank.
    mobiles_dropped: int = 0
    lines_created: int = 0
    lines_skipped: int = 0
    #: Converted leads created for customers that had none yet.
    leads_created: int = 0
    #: Accounts moved to a different owner because the file's Sales Person changed.
    owners_changed: int = 0
    error_count: int = 0
    errors: list[dict] = field(default_factory=list)
    unmatched_sales_people: set[str] = field(default_factory=set)
    #: Portal customers this file does not mention. Left untouched.
    not_in_file: list[str] = field(default_factory=list)
    import_id: uuid.UUID | None = None

    @property
    def status(self) -> str:
        if self.error_count and not (self.lines_created or self.lines_skipped):
            return ImportStatus.FAILED
        if self.error_count:
            return ImportStatus.PARTIAL
        return ImportStatus.SUCCESS

    def as_dict(self) -> dict:
        return {
            "filename": self.filename,
            "total_rows": self.total_rows,
            "customers_created": self.customers_created,
            "customers_updated": self.customers_updated,
            "lines_created": self.lines_created,
            "lines_skipped": self.lines_skipped,
            "leads_created": self.leads_created,
            "owners_changed": self.owners_changed,
            "error_count": self.error_count,
            "errors": self.errors,
            "unmatched_sales_people": sorted(self.unmatched_sales_people),
            "not_in_file": self.not_in_file,
            "status": self.status,
            "import_id": str(self.import_id) if self.import_id else None,
        }


def _row_problem(row: CustomerRow) -> str | None:
    """Why a row cannot be applied, or None. Checked before anything is written."""
    if not row.sap_code:
        return "Row has no customer code."
    if not row.invoice_no:
        return "Row has no invoice number / FGPO code."
    if not row.name:
        return "Row has no customer name."
    if row.invoice_date is None:
        return "Invoice Date is missing or is not a date."
    return None


def import_rows(
    db: Session,
    rows: Iterable[CustomerRow],
    *,
    filename: str,
    actor: User | None = None,
    link_leads: bool | None = None,
) -> ImportResult:
    """Apply an iterable of SAP lines. Caller commits.

    `link_leads` (default: settings.SAP_LINK_LEADS) also gives every customer a
    converted lead assigned to its salesperson - see `link_customer_leads`.
    """
    if link_leads is None:
        link_leads = settings.SAP_LINK_LEADS
    result = ImportResult(filename=filename)
    users = db.execute(select(User)).scalars().all()
    owners = {_match_key(u.name): u for u in users}
    names: dict[uuid.UUID | None, str] = {u.id: u.name for u in users}

    batch = SapImport(
        filename=filename,
        source=SapImportSource.SAP_FILE,
        uploaded_by_user_id=actor.id if actor else None,
        column_map=dict(COLUMN_MAP),
    )
    db.add(batch)
    db.flush()
    result.import_id = batch.id

    # Group by customer first: a customer is applied as a whole, from all of
    # its rows, or not at all.
    groups: dict[str, list[tuple[int, CustomerRow]]] = {}
    rejected: set[str] = set()
    for index, row in enumerate(rows, start=2):    # 2 = first row under the header
        result.total_rows += 1
        problem = _row_problem(row)
        if problem:
            result.error_count += 1
            result.errors.append(
                {"row": index, "customer": row.sap_code or None, "message": problem}
            )
            if row.sap_code:
                rejected.add(row.sap_code)
            continue
        groups.setdefault(row.sap_code, []).append((index, row))
    for code in rejected:
        if groups.pop(code, None) is not None:
            result.errors.append(
                {
                    "customer": code,
                    "type": "skipped",
                    "message": "Customer left unchanged until its rows are fixed.",
                }
            )

    customers: dict[str, Customer] = {}
    # sap_code -> previous owner id, for customers the file reassigned.
    reassigned: dict[str, uuid.UUID | None] = {}

    for code, group in groups.items():
        # The latest invoice carries the account's current details; ties go to
        # the row lower down the sheet.
        _, latest = max(
            enumerate(group), key=lambda item: (item[1][1].invoice_date, item[0])
        )[1]

        mobile, mobile_dropped = _clean_mobile(latest.mobile)
        if mobile_dropped:
            result.mobiles_dropped += 1
            result.errors.append(
                {
                    "row": group[-1][0],
                    "customer": code,
                    "type": "skipped",
                    "column": "Mobile",
                    "message": "Not a 10-digit mobile number - imported without it.",
                }
            )

        owner = owners.get(_match_key(latest.sales_person)) if latest.sales_person else None
        if latest.sales_person and owner is None:
            result.unmatched_sales_people.add(latest.sales_person)

        customer = db.execute(
            select(Customer).where(Customer.sap_code == code)
        ).scalar_one_or_none()
        if customer is None:
            customer = Customer(
                sap_code=code,
                name=latest.name,
                mobile=mobile,
                email=latest.email,
                owner_user_id=owner.id if owner else None,
                sap_sales_person=latest.sales_person,
                is_converted=True,
                import_id=batch.id,
            )
            db.add(customer)
            db.flush()
            result.customers_created += 1
        else:
            changed = False
            for attribute, value in (
                ("name", latest.name),
                ("mobile", mobile),
                ("email", latest.email),
            ):
                if getattr(customer, attribute) != value:
                    setattr(customer, attribute, value)
                    changed = True

            if _match_key(customer.sap_sales_person) != _match_key(latest.sales_person):
                customer.sap_sales_person = latest.sales_person
                changed = True
                # Changed in the file since the last import: that is a newer
                # decision than any portal reassignment, so it wins. An
                # unrecognised name never unassigns anybody.
                if owner is not None and customer.owner_user_id != owner.id:
                    reassigned[code] = customer.owner_user_id
                    customer.owner_user_id = owner.id
                    result.owners_changed += 1
            elif customer.owner_user_id is None and owner is not None:
                customer.owner_user_id = owner.id
                changed = True
            if changed:
                result.customers_updated += 1
        customers[code] = customer

        # Lines: every invoice SAP has ever reported is kept. The sheet is a
        # Power Query over SAP, not typed by hand, so a different FGPO or date
        # for a customer is a NEW invoice (or a different query window) - never
        # a correction. Dropping the old line would erase history and move the
        # reference clock.
        existing = {
            line.source_row_hash: line
            for line in db.execute(
                select(InvoiceLine).where(InvoiceLine.customer_id == customer.id)
            ).scalars()
        }
        wanted: set[str] = set()
        for _, row in group:
            digest = row_hash(row)
            if digest in wanted:
                result.lines_skipped += 1
                continue
            wanted.add(digest)
            line = existing.get(digest)
            if line is not None:
                result.lines_skipped += 1
                line.sales_person = row.sales_person
                line.owner_user_id = customer.owner_user_id
                line.reference_date = row.reference_date
                continue
            db.add(
                InvoiceLine(
                    customer_id=customer.id,
                    invoice_no=row.invoice_no,
                    invoice_date=row.invoice_date,
                    reference_date=row.reference_date,
                    fgpo_code=row.fgpo_code,
                    item_description=row.item_description,
                    sales_person=row.sales_person,
                    owner_user_id=customer.owner_user_id,
                    source_row_hash=digest,
                    import_id=batch.id,
                )
            )
            result.lines_created += 1

    db.flush()
    _refresh_rollups(db, customers.keys())
    if link_leads:
        result.leads_created = link_customer_leads(
            db, customers.values(), actor=actor, reassigned=reassigned, names=names
        )

    in_file = set(groups) | rejected
    result.not_in_file = sorted(
        code
        for code in db.execute(select(Customer.sap_code)).scalars()
        if code not in in_file
    )

    batch.total_rows = result.total_rows
    batch.created_count = result.lines_created
    batch.updated_count = result.customers_updated
    batch.skipped_count = result.lines_skipped
    batch.error_count = result.error_count
    batch.errors = result.errors or None
    batch.status = result.status
    db.flush()

    audit.record(
        db,
        actor_id=actor.id if actor else None,
        action=AuditAction.SAP_IMPORTED,
        entity_type=EntityType.IMPORT,
        entity_id=batch.id,
        after={
            "filename": filename,
            "customers_created": result.customers_created,
            "customers_updated": result.customers_updated,
            "lines_created": result.lines_created,
            "owners_changed": result.owners_changed,
            "leads_created": result.leads_created,
            "error_count": result.error_count,
        },
    )
    return result


def _refresh_rollups(db: Session, sap_codes: Iterable[str]) -> None:
    """Recompute first/last invoice date and the invoice count.

    Derived from the stored lines rather than from this batch, so a partial
    re-import cannot leave a customer claiming fewer invoices than it has.
    invoice_count counts distinct invoice numbers, not lines: an invoice with
    three items is one invoice.
    """
    codes = list(sap_codes)
    if not codes:
        return
    customers = (
        db.execute(select(Customer).where(Customer.sap_code.in_(codes))).scalars().all()
    )
    for customer in customers:
        lines = (
            db.execute(
                select(InvoiceLine).where(InvoiceLine.customer_id == customer.id)
            )
            .scalars()
            .all()
        )
        dates = sorted(line.invoice_date for line in lines if line.invoice_date)
        customer.first_invoice_date = dates[0] if dates else None
        customer.last_invoice_date = dates[-1] if dates else None
        customer.invoice_count = len({line.invoice_no for line in lines})
    db.flush()


#: post_sale_records.external_ref for the lead a SAP customer is worked through.
SAP_LEAD_REF_PREFIX = "SAP:"


def _first_reference_date(db: Session, customer: Customer):
    """The Reference Date the workbook gave for this customer's FIRST invoice.

    Keyed to the first invoice because that is the invoice eligibility runs
    from; a later order must not move an account that was already askable.
    """
    line = db.execute(
        select(InvoiceLine)
        .where(InvoiceLine.customer_id == customer.id)
        .order_by(InvoiceLine.invoice_date.asc())
        .limit(1)
    ).scalars().first()
    return line.reference_date if line else None


def link_customer_leads(
    db: Session,
    customers: Iterable[Customer],
    *,
    actor: User | None = None,
    reassigned: dict[str, uuid.UUID | None] | None = None,
    names: dict[uuid.UUID | None, str] | None = None,
) -> int:
    """Give each SAP customer a converted lead, assigned to its salesperson.

    Assigned Leads, Reference Tracking and the feedback queue all work on
    leads, and read a won lead's invoice date from its post-sale record. So
    each customer gets one CONVERTED lead owned by the SAP Sales Person, plus
    a MATCHED post-sale record carrying the first invoice date - which is what
    starts the ten-day reference/feedback clock (core.eligibility).

    Keyed on `SAP:<code>` in post_sale_records.external_ref, so re-importing
    updates rather than duplicates. The lead follows the customer: when the
    file's Sales Person changed (`reassigned`), the lead moves too; otherwise
    a portal reassignment of the lead is left alone.

    Returns the number of leads created.
    """
    reassigned = reassigned or {}
    names = names or {}
    now = utcnow()
    created = 0
    for customer in customers:
        ref = f"{SAP_LEAD_REF_PREFIX}{customer.sap_code}"
        record = db.execute(
            select(PostSaleRecord).where(PostSaleRecord.external_ref == ref)
        ).scalar_one_or_none()
        lead = db.get(Lead, record.lead_id) if record and record.lead_id else None

        if lead is None:
            lead = Lead(
                name=customer.name[:120],
                company_name=customer.name,
                mobile=customer.mobile,
                email=customer.email,
                requirement=f"SAP customer {customer.sap_code}",
                origin=LeadOrigin.ASSIGNED_BY_HEAD,
                status=LeadStatus.CONVERTED,
                assigned_to_user_id=customer.owner_user_id,
                assigned_by_user_id=actor.id if actor else None,
                assigned_at=now,
                closed_at=now,
                dispatched_at=now,
            )
            lead.activities.append(
                LeadActivity(
                    actor_user_id=actor.id if actor else None,
                    activity_type=LeadActivityType.ASSIGNED,
                    to_status=LeadStatus.CONVERTED,
                    remark=(
                        f"Imported from SAP ({customer.sap_code}), sales person "
                        f"{customer.sap_sales_person or 'not set'}."
                    ),
                )
            )
            db.add(lead)
            db.flush()
            created += 1
        else:
            # The lead's name was taken from SAP; follow a rename there unless
            # somebody has since edited it in the portal.
            if record is not None and lead.name == (record.customer_name or "")[:120]:
                lead.name = customer.name[:120]
            for attribute, value in (
                ("company_name", customer.name),
                ("mobile", customer.mobile),
                ("email", customer.email),
            ):
                if getattr(lead, attribute) != value:
                    setattr(lead, attribute, value)

            if (
                customer.sap_code in reassigned
                and lead.assigned_to_user_id != customer.owner_user_id
            ):
                previous = lead.assigned_to_user_id
                lead.assigned_to_user_id = customer.owner_user_id
                lead.assigned_by_user_id = actor.id if actor else None
                lead.assigned_at = now
                lead.activities.append(
                    LeadActivity(
                        actor_user_id=actor.id if actor else None,
                        activity_type=LeadActivityType.REASSIGNED,
                        remark=(
                            f"Sales Person changed in SAP: reassigned from "
                            f"{names.get(previous, 'nobody')} to "
                            f"{names.get(customer.owner_user_id, 'nobody')}."
                        ),
                    )
                )
            elif lead.assigned_to_user_id is None and customer.owner_user_id is not None:
                lead.assigned_to_user_id = customer.owner_user_id
                lead.assigned_at = now

        if record is None:
            record = PostSaleRecord(external_ref=ref, source="SAP_IMPORT")
            db.add(record)
        record.lead_id = lead.id
        record.status = "MATCHED"
        record.matched_on = "SAP_CODE"
        record.invoice_date = customer.first_invoice_date
        # The sheet's own Reference Date for the invoice the clock runs from -
        # the FIRST one, matching customer.first_invoice_date above.
        record.reference_date = _first_reference_date(db, customer)
        record.customer_name = customer.name
        record.company_name = customer.name
        record.mobile = customer.mobile
        record.email = customer.email
        record.synced_at = now
    db.flush()
    return created


def import_source(
    db: Session,
    source: CustomerSource,
    *,
    actor: User | None = None,
    link_leads: bool | None = None,
) -> ImportResult:
    return import_rows(
        db, source.rows(), filename=source.name, actor=actor, link_leads=link_leads
    )
