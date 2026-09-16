"""Customer references - one row per ask."""
from __future__ import annotations

import uuid
from datetime import date, datetime

from sqlalchemy import Date, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import GUID, Base, TimestampType, new_uuid, utcnow


class CustomerReference(Base):
    __tablename__ = "customer_references"

    id: Mapped[uuid.UUID] = mapped_column(GUID, primary_key=True, default=new_uuid)
    # Exactly one of these is set, enforced by ck_customer_references_subject.
    # A SAP customer and a converted lead are both won deals worth asking, and
    # they stay in their own tables — see 0004_lead_references.sql.
    customer_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID, ForeignKey("customers.id", ondelete="CASCADE")
    )
    lead_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID, ForeignKey("leads.id", ondelete="CASCADE")
    )
    # The attribution field. Scoped by visible_user_ids on every list, and
    # settable only to someone the actor can act on.
    requested_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID, ForeignKey("users.id", ondelete="SET NULL")
    )

    outcome: Mapped[str] = mapped_column(String(20), nullable=False)
    asked_on: Mapped[date] = mapped_column(Date, nullable=False)
    # Required when outcome is NO: "ask me later" without a date is not a
    # follow-up, it is a lost thread. Enforced by a CHECK in the schema.
    next_reference_date: Mapped[date | None] = mapped_column(Date)

    referred_name: Mapped[str | None] = mapped_column(String(120))
    referred_company: Mapped[str | None] = mapped_column(String(200))
    referred_mobile: Mapped[str | None] = mapped_column(String(30))
    referred_email: Mapped[str | None] = mapped_column(String(255))
    notes: Mapped[str | None] = mapped_column(Text)

    converted_lead_id: Mapped[uuid.UUID | None] = mapped_column(GUID)

    created_at: Mapped[datetime] = mapped_column(
        TimestampType, nullable=False, default=utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        TimestampType, nullable=False, default=utcnow, onupdate=utcnow
    )

    # Only one of these resolves on any given row; the other is None.
    customer: Mapped["Customer"] = relationship("Customer")  # noqa: F821
    # foreign_keys is required: leads.origin_reference_id points back here, so
    # there are two FK paths between these two tables and SQLAlchemy will not
    # guess which one this relationship travels.
    lead: Mapped["Lead"] = relationship("Lead", foreign_keys=[lead_id])  # noqa: F821
