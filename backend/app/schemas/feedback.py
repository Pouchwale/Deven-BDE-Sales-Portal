"""Feedback payloads."""
from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, model_validator

from app.schemas.common import ORMModel


class DepartmentRatingOut(ORMModel):
    """ORM-compatible so FeedbackOut can validate straight off the model.

    `department_name` is not on the rating row — the API fills it in from one
    lookup, and the same pass drops any department outside the caller's scope.
    """

    department_id: uuid.UUID
    department_name: str = "—"
    rating: Decimal | None = None
    raw_value: str | None = None
    comments: str | None = None


class FeedbackOut(ORMModel):
    id: uuid.UUID
    source: str
    submitted_at_source: datetime | None = None
    customer_name: str | None = None
    company_name: str | None = None
    mobile: str | None = None
    email: str | None = None
    handled_by_name: str | None = None
    handled_by_user_id: uuid.UUID | None = None
    overall_rating: Decimal | None = None
    overall_rating_raw: str | None = None
    overall_comments: str | None = None
    would_recommend: str | None = None
    created_at: datetime

    department_ratings: list[DepartmentRatingOut] = []

    # True for the demonstration batch from app/seeds/sample_feedback.py. The
    # UI badges these: a made-up opinion that reads as a real customer's would
    # be worse than showing nothing at all.
    is_sample: bool = False


class DepartmentSummary(BaseModel):
    department_id: uuid.UUID
    department_name: str
    average_rating: float | None = None
    response_count: int
    below_threshold: bool
    enough_responses: bool


class AlertOut(ORMModel):
    id: uuid.UUID
    department_id: uuid.UUID
    department_name: str | None = None
    status: str
    average_rating: Decimal
    response_count: int
    threshold: Decimal
    window_days: int
    opened_at: datetime
    resolved_at: datetime | None = None
    resolved_reason: str | None = None
    assigned_to_user_id: uuid.UUID | None = None
    assigned_to_name: str | None = None


class AlertAssign(BaseModel):
    # None clears the assignment.
    user_id: uuid.UUID | None = None


class PendingFeedbackItem(BaseModel):
    """Someone still owed feedback — a dispatched lead or a converted
    customer with no response on file yet. Two sources, one queue.

    `state` is what requests-as-records bought: NOT_ASKED and AWAITING used
    to look identical from here.
    """

    type: str  # "LEAD" or "CUSTOMER"
    id: uuid.UUID
    name: str
    company_name: str | None = None
    mobile: str | None = None
    email: str | None = None
    since: datetime
    owner_user_id: uuid.UUID | None = None
    owner_name: str | None = None

    state: str = "NOT_ASKED"  # NOT_ASKED | AWAITING
    request_id: uuid.UUID | None = None
    request_reference: str | None = None
    requested_at: datetime | None = None


class MonthlyVolume(BaseModel):
    month: str
    responses: int


class FeedbackStats(BaseModel):
    total_responses: int
    imported_responses: int
    average_rating: float | None = None
    rating_scale_max: int
    departments_below_threshold: int
    open_alerts: int
    would_recommend_rate: float | None = None


class FeedbackAnalysis(BaseModel):
    # Whether this caller is one of the people who work the alert queue.
    # Sent rather than inferred: "alerts is empty" is also true for an admin
    # on a good week, and hiding their tab then would be wrong.
    handles_alerts: bool = False
    stats: FeedbackStats
    departments: list[DepartmentSummary]
    alerts: list[AlertOut]
    monthly: list[MonthlyVolume]
    threshold: float
    window_days: int
    scale_max: int


class ImportSummary(ORMModel):
    id: uuid.UUID
    filename: str
    source: str
    status: str
    total_rows: int
    created_count: int
    skipped_count: int
    error_count: int
    created_at: datetime
    uploaded_by_user_id: uuid.UUID | None = None
    # Row-level detail for both hard errors and duplicate skips, so an admin
    # can see WHY a row didn't land instead of just a count.
    errors: list[dict] | None = None


class CommitRequest(BaseModel):
    """Confirms the mapping the dry run showed."""

    confirmed: bool = True
    create_missing_departments: bool = True


# ------------------------------------------------------------ google sync
class SyncStatus(BaseModel):
    """What an admin needs to tell "working" from "not set up".

    Booleans only. The shared secret is never returned, not even its length.
    """

    webhook_configured: bool
    pull_configured: bool
    form_url_configured: bool
    reference_field_configured: bool
    last_delivery_at: datetime | None = None
    processed: int = 0
    failed: int = 0
    duplicates: int = 0
    needs_review: int = 0


class SyncEventOut(ORMModel):
    """One delivery. A hash and an outcome - never the payload."""

    id: uuid.UUID
    external_response_id: str
    source: str
    status: str
    payload_hash: str
    feedback_id: uuid.UUID | None = None
    error_code: str | None = None
    error_detail: str | None = None
    received_at: datetime
    processed_at: datetime | None = None


class SyncResult(BaseModel):
    new_responses: int = 0
    already_synced: int = 0
    unmatched: int = 0
    errors: int = 0


class UnmatchedResolveBody(BaseModel):
    """File an unmatched response by hand - under a lead's open ask (the
    portal's own records) or, for history, an archived SAP customer.

    Exactly one. A response filed under both would count twice.
    """

    request_id: uuid.UUID | None = None
    customer_id: uuid.UUID | None = None

    @model_validator(mode="after")
    def _exactly_one(self) -> "UnmatchedResolveBody":
        if (self.request_id is None) == (self.customer_id is None):
            raise ValueError("Choose exactly one: a lead's open request or an archived customer.")
        return self
