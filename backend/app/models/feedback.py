"""Customer feedback, its import batches, department ratings and alerts."""
from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import ForeignKey, Integer, Numeric, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.constants import (
    AlertStatus,
    FeedbackImportSource,
    FeedbackMatchStatus,
    FeedbackRequestStatus,
    FeedbackSource,
    ImportStatus,
    SyncEventStatus,
)
from app.db.base import GUID, Base, JSONType, TimestampType, new_uuid, utcnow


class FeedbackImport(Base):
    """One uploaded Google Forms export.

    column_map stores the mapping the admin confirmed in the dry run, so when
    somebody edits the form and the next import looks wrong, the difference is
    visible rather than a mystery.
    """

    __tablename__ = "feedback_imports"

    id: Mapped[uuid.UUID] = mapped_column(GUID, primary_key=True, default=new_uuid)
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    source: Mapped[str] = mapped_column(
        String(30), nullable=False, default=FeedbackImportSource.GOOGLE_FORMS_XLSX
    )
    uploaded_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID, ForeignKey("users.id", ondelete="SET NULL")
    )
    column_map: Mapped[dict | None] = mapped_column(JSONType)
    total_rows: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    skipped_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    errors: Mapped[list | None] = mapped_column(JSONType)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default=ImportStatus.SUCCESS
    )
    created_at: Mapped[datetime] = mapped_column(
        TimestampType, nullable=False, default=utcnow
    )


class Feedback(Base):
    __tablename__ = "feedback"

    id: Mapped[uuid.UUID] = mapped_column(GUID, primary_key=True, default=new_uuid)
    # The idempotency key: sha256 of the normalised source row.
    source_row_hash: Mapped[str | None] = mapped_column(String(64), unique=True)
    import_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID, ForeignKey("feedback_imports.id", ondelete="SET NULL")
    )
    source: Mapped[str] = mapped_column(
        String(30), nullable=False, default=FeedbackSource.GOOGLE_FORMS_IMPORT
    )

    submitted_at_source: Mapped[datetime | None] = mapped_column(TimestampType)
    customer_name: Mapped[str | None] = mapped_column(String(200))
    company_name: Mapped[str | None] = mapped_column(String(200))
    mobile: Mapped[str | None] = mapped_column(String(30))
    email: Mapped[str | None] = mapped_column(String(255))

    # Resolved on import by matching mobile, email or company against the SAP
    # book. Nullable: an unmatched response is still a response.
    customer_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID, ForeignKey("customers.id", ondelete="SET NULL")
    )

    handled_by_name: Mapped[str | None] = mapped_column(String(120))
    handled_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID, ForeignKey("users.id", ondelete="SET NULL")
    )

    overall_rating: Mapped[Decimal | None] = mapped_column(Numeric(4, 2))
    overall_rating_raw: Mapped[str | None] = mapped_column(String(40))
    overall_comments: Mapped[str | None] = mapped_column(Text)
    would_recommend: Mapped[str | None] = mapped_column(String(20))

    # Set when a response matched the ask that caused it. Null for anything
    # that arrived by file import or could not be matched.
    feedback_request_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID, ForeignKey("feedback_requests.id", ondelete="SET NULL")
    )
    match_status: Mapped[str] = mapped_column(
        String(20), nullable=False, default=FeedbackMatchStatus.MATCHED_CONTACT
    )
    external_response_id: Mapped[str | None] = mapped_column(String(128))

    created_at: Mapped[datetime] = mapped_column(
        TimestampType, nullable=False, default=utcnow
    )

    department_ratings: Mapped[list[FeedbackDepartmentRating]] = relationship(
        "FeedbackDepartmentRating",
        back_populates="feedback",
        cascade="all, delete-orphan",
    )


class FeedbackDepartmentRating(Base):
    """One department's score on one response.

    raw_value keeps the source wording next to the normalised number, so
    changing the scale later cannot silently rewrite history.
    """

    __tablename__ = "feedback_department_ratings"

    id: Mapped[uuid.UUID] = mapped_column(GUID, primary_key=True, default=new_uuid)
    feedback_id: Mapped[uuid.UUID] = mapped_column(
        GUID, ForeignKey("feedback.id", ondelete="CASCADE"), nullable=False
    )
    department_id: Mapped[uuid.UUID] = mapped_column(
        GUID, ForeignKey("departments.id", ondelete="CASCADE"), nullable=False
    )
    rating: Mapped[Decimal | None] = mapped_column(Numeric(4, 2))
    raw_value: Mapped[str | None] = mapped_column(String(40))
    comments: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        TimestampType, nullable=False, default=utcnow
    )

    feedback: Mapped[Feedback] = relationship(
        "Feedback", back_populates="department_ratings"
    )
    department: Mapped["Department"] = relationship("Department")  # noqa: F821


class FeedbackAlert(Base):
    """A department whose rolling average fell below the threshold.

    At most one OPEN row per department, enforced by a partial unique index
    rather than by a read-then-write race in the service.
    """

    __tablename__ = "feedback_alerts"

    id: Mapped[uuid.UUID] = mapped_column(GUID, primary_key=True, default=new_uuid)
    department_id: Mapped[uuid.UUID] = mapped_column(
        GUID, ForeignKey("departments.id", ondelete="CASCADE"), nullable=False
    )
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default=AlertStatus.OPEN
    )
    average_rating: Mapped[Decimal] = mapped_column(Numeric(4, 2), nullable=False)
    response_count: Mapped[int] = mapped_column(Integer, nullable=False)
    threshold: Mapped[Decimal] = mapped_column(Numeric(4, 2), nullable=False)
    window_days: Mapped[int] = mapped_column(Integer, nullable=False)
    opened_at: Mapped[datetime] = mapped_column(
        TimestampType, nullable=False, default=utcnow
    )
    resolved_at: Mapped[datetime | None] = mapped_column(TimestampType)
    resolved_reason: Mapped[str | None] = mapped_column(String(255))
    assigned_to_user_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID, ForeignKey("users.id", ondelete="SET NULL")
    )

    department: Mapped["Department"] = relationship("Department")  # noqa: F821


class FeedbackRequest(Base):
    """One ask, and the thing a Google Form response points back at.

    Created when somebody presses Send - not for every askable record. "Not
    yet asked" stays the absence of a row; see the migration for why.
    """

    __tablename__ = "feedback_requests"

    id: Mapped[uuid.UUID] = mapped_column(GUID, primary_key=True, default=new_uuid)

    #: Human-readable, quoted on the phone and legible in the response sheet.
    reference: Mapped[str] = mapped_column(String(20), nullable=False, unique=True)
    #: What matching actually trusts. A guessable reference alone would let a
    #: stranger attach a response to somebody else's account.
    token: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)

    subject_type: Mapped[str] = mapped_column(String(10), nullable=False)
    lead_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID, ForeignKey("leads.id", ondelete="CASCADE")
    )
    customer_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID, ForeignKey("customers.id", ondelete="CASCADE")
    )
    owner_user_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID, ForeignKey("users.id", ondelete="SET NULL")
    )

    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default=FeedbackRequestStatus.ISSUED
    )
    channel: Mapped[str | None] = mapped_column(String(20))

    sent_at: Mapped[datetime] = mapped_column(
        TimestampType, nullable=False, default=utcnow
    )
    completed_at: Mapped[datetime | None] = mapped_column(TimestampType)
    expires_at: Mapped[datetime | None] = mapped_column(TimestampType)
    created_at: Mapped[datetime] = mapped_column(
        TimestampType, nullable=False, default=utcnow
    )

    @property
    def code(self) -> str:
        """What goes into the form field: `FB-2026-00127.k7x9m2…`."""
        return f"{self.reference}.{self.token}"


class FeedbackSyncEvent(Base):
    """One inbound delivery, whether or not it became feedback.

    Written and committed BEFORE processing, so a crash mid-ingest leaves a
    record rather than losing a customer's response.
    """

    __tablename__ = "feedback_sync_events"

    id: Mapped[uuid.UUID] = mapped_column(GUID, primary_key=True, default=new_uuid)
    #: Google's own response id. Unique - this IS the idempotency guarantee.
    external_response_id: Mapped[str] = mapped_column(
        String(128), nullable=False, unique=True
    )
    source: Mapped[str] = mapped_column(String(20), nullable=False)
    #: The hash, never the payload: a response carries a name, a mobile, an
    #: email and free text, none of which belongs in a "did this arrive?" log.
    payload_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default=SyncEventStatus.RECEIVED
    )
    feedback_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID, ForeignKey("feedback.id", ondelete="SET NULL")
    )
    error_code: Mapped[str | None] = mapped_column(String(40))
    error_detail: Mapped[str | None] = mapped_column(String(500))
    received_at: Mapped[datetime] = mapped_column(
        TimestampType, nullable=False, default=utcnow
    )
    processed_at: Mapped[datetime | None] = mapped_column(TimestampType)
