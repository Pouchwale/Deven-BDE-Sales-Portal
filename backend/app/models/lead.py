"""Leads and their activity timeline."""
from __future__ import annotations

import uuid
from datetime import date, datetime

from sqlalchemy import Date, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.constants import LeadOrigin, LeadPriority, LeadStatus, ReferenceStatus
from app.db.base import GUID, Base, TimestampType, new_uuid, utcnow


class Lead(Base):
    __tablename__ = "leads"

    id: Mapped[uuid.UUID] = mapped_column(GUID, primary_key=True, default=new_uuid)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    company_name: Mapped[str | None] = mapped_column(String(200))
    mobile: Mapped[str | None] = mapped_column(String(30))
    email: Mapped[str | None] = mapped_column(String(255))
    city: Mapped[str | None] = mapped_column(String(80))
    requirement: Mapped[str | None] = mapped_column(Text)

    # Labels which of the two kinds of work this is. Reference follow-ups are
    # surfaced from the reference module and are not lead rows, so in practice
    # this is ASSIGNED_BY_HEAD until that decision is ever revisited - at which
    # point it becomes a data change rather than a migration.
    origin: Mapped[str] = mapped_column(
        String(30), nullable=False, default=LeadOrigin.ASSIGNED_BY_HEAD
    )
    origin_reference_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID, ForeignKey("customer_references.id", ondelete="SET NULL")
    )

    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default=LeadStatus.NEW
    )
    priority: Mapped[str] = mapped_column(
        String(10), nullable=False, default=LeadPriority.MEDIUM
    )

    assigned_to_user_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID, ForeignKey("users.id", ondelete="SET NULL")
    )
    assigned_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID, ForeignKey("users.id", ondelete="SET NULL")
    )
    assigned_at: Mapped[datetime | None] = mapped_column(TimestampType)
    next_follow_up_date: Mapped[date | None] = mapped_column(Date)
    closed_at: Mapped[datetime | None] = mapped_column(TimestampType)
    # Stamped when the lead reaches CONVERTED. That is what makes it eligible
    # for a feedback ask, and for a reference ask below.
    dispatched_at: Mapped[datetime | None] = mapped_column(TimestampType)

    # The same reference roll-up a customer carries, so one follow-up queue
    # reads both. A converted lead is a won deal and can be asked to refer
    # somebody, exactly like an invoiced SAP account.
    reference_status: Mapped[str] = mapped_column(
        String(20), nullable=False, default=ReferenceStatus.NOT_ASKED
    )
    last_reference_asked_at: Mapped[date | None] = mapped_column(Date)
    next_reference_date: Mapped[date | None] = mapped_column(Date)

    created_at: Mapped[datetime] = mapped_column(
        TimestampType, nullable=False, default=utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        TimestampType, nullable=False, default=utcnow, onupdate=utcnow
    )

    activities: Mapped[list[LeadActivity]] = relationship(
        "LeadActivity",
        back_populates="lead",
        cascade="all, delete-orphan",
        order_by="LeadActivity.created_at",
    )

    def __repr__(self) -> str:
        return f"<Lead {self.name} ({self.status})>"


class LeadActivity(Base):
    __tablename__ = "lead_activities"

    id: Mapped[uuid.UUID] = mapped_column(GUID, primary_key=True, default=new_uuid)
    lead_id: Mapped[uuid.UUID] = mapped_column(
        GUID, ForeignKey("leads.id", ondelete="CASCADE"), nullable=False
    )
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID, ForeignKey("users.id", ondelete="SET NULL")
    )
    activity_type: Mapped[str] = mapped_column(String(30), nullable=False)
    from_status: Mapped[str | None] = mapped_column(String(20))
    to_status: Mapped[str | None] = mapped_column(String(20))
    remark: Mapped[str | None] = mapped_column(Text)
    # Soft undo: the row stays so the trail reads "this happened, then it was
    # taken back", which is the truth. Deleting it would hide a real event.
    undone_at: Mapped[datetime | None] = mapped_column(TimestampType)
    undone_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID, ForeignKey("users.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = mapped_column(
        TimestampType, nullable=False, default=utcnow
    )

    lead: Mapped[Lead] = relationship("Lead", back_populates="activities")

    @property
    def is_undone(self) -> bool:
        return self.undone_at is not None
