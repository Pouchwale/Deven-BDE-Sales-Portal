"""Converted customers and the SAP invoice lines they come from."""
from __future__ import annotations

import uuid
from datetime import date, datetime

from sqlalchemy import Boolean, Date, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.constants import ImportStatus, ReferenceStatus, SapImportSource
from app.db.base import GUID, Base, JSONType, TimestampType, new_uuid, utcnow


class SapImport(Base):
    """One ingest of a SAP extract. Keeps the resolved column mapping."""

    __tablename__ = "sap_imports"

    id: Mapped[uuid.UUID] = mapped_column(GUID, primary_key=True, default=new_uuid)
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    source: Mapped[str] = mapped_column(
        String(30), nullable=False, default=SapImportSource.SAP_FILE
    )
    uploaded_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID, ForeignKey("users.id", ondelete="SET NULL")
    )
    column_map: Mapped[dict | None] = mapped_column(JSONType)
    total_rows: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    updated_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    skipped_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    errors: Mapped[list | None] = mapped_column(JSONType)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default=ImportStatus.SUCCESS
    )
    created_at: Mapped[datetime] = mapped_column(
        TimestampType, nullable=False, default=utcnow
    )


class Customer(Base):
    """A customer SAP has invoiced, i.e. a converted account."""

    __tablename__ = "customers"

    id: Mapped[uuid.UUID] = mapped_column(GUID, primary_key=True, default=new_uuid)
    sap_code: Mapped[str] = mapped_column(String(30), nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    mobile: Mapped[str | None] = mapped_column(String(30))
    email: Mapped[str | None] = mapped_column(String(255))

    # NULL means unowned: visible to ADMIN / SUPER_ADMIN only, and the pool a
    # manager assigns from.
    owner_user_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID, ForeignKey("users.id", ondelete="SET NULL")
    )
    # Kept verbatim even when it does not resolve to a portal account, so an
    # unmatched salesperson is visible rather than silently dropped.
    sap_sales_person: Mapped[str | None] = mapped_column(String(120))

    is_converted: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    first_invoice_date: Mapped[date | None] = mapped_column(Date)
    last_invoice_date: Mapped[date | None] = mapped_column(Date)
    invoice_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    reference_status: Mapped[str] = mapped_column(
        String(20), nullable=False, default=ReferenceStatus.NOT_ASKED
    )
    last_reference_asked_at: Mapped[date | None] = mapped_column(Date)
    next_reference_date: Mapped[date | None] = mapped_column(Date)

    import_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID, ForeignKey("sap_imports.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = mapped_column(
        TimestampType, nullable=False, default=utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        TimestampType, nullable=False, default=utcnow, onupdate=utcnow
    )

    invoice_lines: Mapped[list[InvoiceLine]] = relationship(
        "InvoiceLine", back_populates="customer", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:
        return f"<Customer {self.sap_code} {self.name}>"


class InvoiceLine(Base):
    """One line of one SAP invoice.

    An invoice number repeats across its lines, so the idempotency key is a
    hash of the whole normalised source row rather than the invoice number.
    """

    __tablename__ = "invoice_lines"

    id: Mapped[uuid.UUID] = mapped_column(GUID, primary_key=True, default=new_uuid)
    customer_id: Mapped[uuid.UUID] = mapped_column(
        GUID, ForeignKey("customers.id", ondelete="CASCADE"), nullable=False
    )
    invoice_no: Mapped[str] = mapped_column(String(40), nullable=False)
    invoice_date: Mapped[date | None] = mapped_column(Date)
    #: The workbook's own Reference Date column - the day this account may be
    #: asked. Stored rather than recomputed; see core/eligibility.py.
    reference_date: Mapped[date | None] = mapped_column(Date)
    fgpo_code: Mapped[str | None] = mapped_column(String(40))
    item_description: Mapped[str | None] = mapped_column(Text)
    sales_person: Mapped[str | None] = mapped_column(String(120))
    owner_user_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID, ForeignKey("users.id", ondelete="SET NULL")
    )
    source_row_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    import_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID, ForeignKey("sap_imports.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = mapped_column(
        TimestampType, nullable=False, default=utcnow
    )

    customer: Mapped[Customer] = relationship("Customer", back_populates="invoice_lines")


class CustomerActivity(Base):
    """The timeline of a COMPLETED customer.

    A customer that arrived from SAP has already finished the lead pipeline —
    it is a won deal, not a prospect. What is still open about it is the
    feedback and the reference, so it gets a timeline of its own rather than
    being forced through stages it has already passed.

    Reference asks are NOT duplicated here; they live in customer_references
    and are merged into the same view at read time.
    """

    __tablename__ = "customer_activities"

    id: Mapped[uuid.UUID] = mapped_column(GUID, primary_key=True, default=new_uuid)
    customer_id: Mapped[uuid.UUID] = mapped_column(
        GUID, ForeignKey("customers.id", ondelete="CASCADE"), nullable=False
    )
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID, ForeignKey("users.id", ondelete="SET NULL")
    )
    activity_type: Mapped[str] = mapped_column(String(30), nullable=False)
    remark: Mapped[str | None] = mapped_column(Text)
    related_type: Mapped[str | None] = mapped_column(String(30))
    related_id: Mapped[uuid.UUID | None] = mapped_column(GUID)

    # Soft undo, same as lead activities: the row stays so the trail reads
    # "this happened, then it was taken back".
    undone_at: Mapped[datetime | None] = mapped_column(TimestampType)
    undone_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID, ForeignKey("users.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = mapped_column(
        TimestampType, nullable=False, default=utcnow
    )

    customer: Mapped[Customer] = relationship("Customer")

    @property
    def is_undone(self) -> bool:
        return self.undone_at is not None
