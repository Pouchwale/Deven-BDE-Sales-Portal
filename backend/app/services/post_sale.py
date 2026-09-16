"""Bringing the company's post-sale sheet into the portal.

THE ONE RULE
------------
A row from the sheet ENRICHES a lead. It never becomes one. If it cannot be
matched to a lead with confidence, it is held as UNMATCHED for somebody to
resolve - it is not attached to whichever lead looked closest, and it does not
quietly create a new record. A reference ask that reaches the wrong customer
because two people share a company name is worse than an unmatched row sitting
in a review queue.

MATCHING, STRONGEST FIRST
-------------------------
1. `external_ref`   the sheet's own id. Exact, and the only one that is
                    unambiguous by construction.
2. `mobile`         compared as ten digits, so +91/spacing differences do not
                    matter - see `core.validators`.
3. `email`          case-insensitive.
4. `company_name`   exact after normalising whitespace and case, and ONLY when
                    it matches exactly one converted lead.

Name alone is never used. Two customers called "Sharma Traders" are two
customers, and picking either is a coin toss dressed up as a match.

Every match is recorded in `matched_on`, so a wrong link can be understood
later rather than re-derived by guesswork.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core import text
from app.core.constants import LeadStatus
from app.core.validators import InvalidPhone, normalise_phone
from app.db.base import utcnow
from app.models.lead import Lead
from app.models.post_sale import PostSaleRecord

MATCHED = "MATCHED"
UNMATCHED = "UNMATCHED"
NEEDS_REVIEW = "NEEDS_REVIEW"

#: Name comparison key. Shared with the SAP importer - see app/core/text.py.
_key = text.match_key


def _digits(value: str | None) -> str:
    try:
        return normalise_phone(value) or ""
    except InvalidPhone:
        # An unusable number is not a matching key. Falling back to raw digits
        # here would match on fragments, which is the opposite of the point.
        return ""


@dataclass
class SyncResult:
    """What one sync did, in terms somebody can act on."""

    total_rows: int = 0
    matched: int = 0
    unmatched: int = 0
    updated: int = 0
    errors: list[dict] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "total_rows": self.total_rows,
            "matched": self.matched,
            "unmatched": self.unmatched,
            "updated": self.updated,
            "errors": self.errors,
        }


class LeadIndex:
    """The converted leads, indexed once per sync rather than per row.

    A sheet with three hundred rows should be a handful of queries, not three
    hundred - and the ambiguity check needs to know how many leads share a
    company name, which a per-row lookup cannot see.
    """

    def __init__(self, db: Session) -> None:
        leads = list(
            db.execute(select(Lead).where(Lead.status == LeadStatus.CONVERTED))
            .scalars()
            .unique()
        )
        self.by_mobile: dict[str, list[Lead]] = {}
        self.by_email: dict[str, list[Lead]] = {}
        self.by_company: dict[str, list[Lead]] = {}
        for lead in leads:
            if (mobile := _digits(lead.mobile)):
                self.by_mobile.setdefault(mobile, []).append(lead)
            if lead.email:
                self.by_email.setdefault(_key(lead.email), []).append(lead)
            if (company := _key(lead.company_name or lead.name)):
                self.by_company.setdefault(company, []).append(lead)

    def find(self, row: dict) -> tuple[Lead | None, str | None]:
        """(lead, how) or (None, None). Never a guess."""
        for index, value, label in (
            (self.by_mobile, _digits(row.get("mobile")), "MOBILE"),
            (self.by_email, _key(row.get("email")), "EMAIL"),
            (self.by_company, _key(row.get("company_name")), "COMPANY"),
        ):
            if not value:
                continue
            candidates = index.get(value, [])
            # Exactly one, or it is not a match. Two leads sharing a company
            # name is precisely the case where guessing does harm.
            if len(candidates) == 1:
                return candidates[0], label
        return None, None


def upsert_row(
    db: Session, index: LeadIndex, row: dict, result: SyncResult
) -> PostSaleRecord:
    """One sheet row in, one post-sale record out.

    Idempotent on `external_ref`: re-syncing the same row updates it rather
    than stacking duplicates, which is what makes a nightly sync safe to run
    twice.
    """
    result.total_rows += 1
    external_ref = (row.get("external_ref") or "").strip() or None

    record: PostSaleRecord | None = None
    if external_ref:
        record = db.execute(
            select(PostSaleRecord).where(PostSaleRecord.external_ref == external_ref)
        ).scalars().first()

    if record is None:
        record = PostSaleRecord(external_ref=external_ref)
        db.add(record)
    else:
        result.updated += 1

    record.customer_name = row.get("customer_name")
    record.company_name = row.get("company_name")
    record.mobile = row.get("mobile")
    record.email = row.get("email")
    record.notes = row.get("notes")
    record.invoice_date = row.get("invoice_date")
    record.synced_at = utcnow()

    lead, matched_on = index.find(row)
    if lead is not None:
        record.lead_id = lead.id
        record.matched_on = matched_on
        record.status = MATCHED
        result.matched += 1
    else:
        # Held, not guessed at, and not silently turned into a new lead.
        record.lead_id = None
        record.matched_on = None
        record.status = UNMATCHED
        result.unmatched += 1

    db.flush()
    return record


def sync_rows(db: Session, rows: list[dict]) -> SyncResult:
    """Bring a batch in. Caller commits."""
    result = SyncResult()
    index = LeadIndex(db)
    for position, row in enumerate(rows, start=1):
        try:
            upsert_row(db, index, row, result)
        except Exception as error:  # noqa: BLE001 - reported, never swallowed
            result.errors.append({"row": position, "message": str(error)[:200]})
    return result


def resolve(
    db: Session, record_id: uuid.UUID, lead_id: uuid.UUID
) -> PostSaleRecord | None:
    """Attach an unmatched row to the lead a human identified.

    The reconciliation path §31 asks for: the portal refuses to guess, and a
    person who knows the account says which one it is.
    """
    record = db.get(PostSaleRecord, record_id)
    if record is None:
        return None
    lead = db.get(Lead, lead_id)
    if lead is None or lead.status != LeadStatus.CONVERTED:
        return None

    record.lead_id = lead.id
    record.status = MATCHED
    record.matched_on = "MANUAL"
    db.flush()
    return record


def unmatched(db: Session, limit: int = 100) -> list[PostSaleRecord]:
    """Rows waiting for a human. Not scoped by reporting line: an unmatched
    row has no lead yet, so there is nobody it could be scoped to - which is
    why resolving them is an administrator's job."""
    return list(
        db.execute(
            select(PostSaleRecord)
            .where(PostSaleRecord.status != MATCHED)
            .order_by(PostSaleRecord.created_at.desc())
            .limit(limit)
        )
        .scalars()
        .unique()
    )


def status_summary(db: Session) -> dict:
    """Whether anything has been synced at all, and how it went.

    `last_synced_at` is the field that lets the UI say "waiting for the next
    sync" instead of showing a bare zero that reads as "no customers".
    """
    rows = db.execute(
        select(PostSaleRecord.status, func.count(PostSaleRecord.id)).group_by(
            PostSaleRecord.status
        )
    ).all()
    counts = {status: count for status, count in rows}
    last = db.scalar(select(func.max(PostSaleRecord.synced_at)))
    return {
        "matched": counts.get(MATCHED, 0),
        "unmatched": counts.get(UNMATCHED, 0),
        "needs_review": counts.get(NEEDS_REVIEW, 0),
        "total": sum(counts.values()),
        "last_synced_at": last,
        "ever_synced": bool(counts),
    }
