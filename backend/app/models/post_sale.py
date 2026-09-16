"""Post-sale records: what happened to a won lead after it was won.

WHY THIS EXISTS
---------------
A lead the portal converted and an account the business invoiced used to be
two unrelated rows in two unrelated tables. `customers` came from SAP and knew
nothing about leads; `leads` knew nothing about invoices. So the portal could
honestly report "22 converted" in one module and "17 accounts" in another, and
both were right about different universes. Nobody could reconcile them because
there was nothing to reconcile them THROUGH.

This table is that link, and it is the only one:

    Lead  ──1:N──  PostSaleRecord  ──  the external sheet

A post-sale record belongs to exactly one lead, or to none yet. It never
becomes a second lead. That is the whole point - enrich, do not duplicate.

WHAT IT CARRIES
---------------
Whatever the company's sheet supplies about a won deal after the sale:
principally the invoice date, which is what makes the account eligible for a
reference ask and a feedback request ten days later.

WHAT IT DOES NOT DO
-------------------
It does not decide anything. `status` records whether the row found its lead;
eligibility is computed from `invoice_date` by `core.eligibility`, in one
place, for every module that asks.
"""
from __future__ import annotations

import uuid
from datetime import date, datetime

from sqlalchemy import Date, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, GUID, TimestampType, new_uuid, utcnow
from app.models.lead import Lead


class PostSaleRecord(Base):
    """One won deal, as the external system knows it."""

    __tablename__ = "post_sale_records"

    id: Mapped[uuid.UUID] = mapped_column(GUID, primary_key=True, default=new_uuid)

    # The link. NULL means the row arrived but could not be matched to a lead
    # safely - see `status`. It is never guessed at.
    lead_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID, ForeignKey("leads.id", ondelete="CASCADE")
    )

    #: The sheet's own stable identifier, if it has one. UNIQUE, so re-syncing
    #: the same row updates it rather than creating a second copy.
    external_ref: Mapped[str | None] = mapped_column(String(128), unique=True)

    #: Where the row came from. EXCEL_SYNC today; the column exists so a second
    #: source can be added without anyone having to guess afterwards.
    source: Mapped[str] = mapped_column(String(30), nullable=False, default="EXCEL_SYNC")

    #: MATCHED / UNMATCHED / NEEDS_REVIEW. An unmatched row is held, not
    #: attached to whichever lead looked closest.
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="UNMATCHED")

    #: How the match was made, kept so a wrong link can be understood later
    #: rather than re-derived: MOBILE / EMAIL / COMPANY / EXTERNAL_REF.
    matched_on: Mapped[str | None] = mapped_column(String(20))

    #: THE date the ten-day eligibility rule runs from. Named for what it is in
    #: the business, not for whichever column the sheet happens to use - the
    #: importer maps the sheet's wording onto this.
    invoice_date: Mapped[date | None] = mapped_column(Date)

    #: The day this account may be asked for a reference, as the SAP workbook
    #: publishes it. NULL for anything that did not come from the workbook, and
    #: then core/eligibility.py falls back to invoice date + 10 days.
    reference_date: Mapped[date | None] = mapped_column(Date)

    #: What the sheet said, kept verbatim. These are the matching inputs and
    #: also what an administrator reads when reconciling an unmatched row.
    customer_name: Mapped[str | None] = mapped_column(String(200))
    company_name: Mapped[str | None] = mapped_column(String(200))
    mobile: Mapped[str | None] = mapped_column(String(30))
    email: Mapped[str | None] = mapped_column(String(255))
    notes: Mapped[str | None] = mapped_column(Text)

    synced_at: Mapped[datetime | None] = mapped_column(TimestampType)
    created_at: Mapped[datetime] = mapped_column(
        TimestampType, nullable=False, default=utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        TimestampType, nullable=False, default=utcnow, onupdate=utcnow
    )

    lead: Mapped[Lead | None] = relationship("Lead", lazy="joined")

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<PostSaleRecord {self.customer_name} ({self.status})>"
