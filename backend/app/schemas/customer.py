"""Converted-customer payloads."""
from __future__ import annotations

import uuid
from datetime import date, datetime

from pydantic import BaseModel

from app.schemas.common import ORMModel


class InvoiceLineOut(ORMModel):
    id: uuid.UUID
    invoice_no: str
    invoice_date: date | None = None
    fgpo_code: str | None = None
    item_description: str | None = None
    sales_person: str | None = None


class CustomerOut(ORMModel):
    id: uuid.UUID
    sap_code: str
    name: str
    mobile: str | None = None
    email: str | None = None
    owner_user_id: uuid.UUID | None = None
    sap_sales_person: str | None = None
    first_invoice_date: date | None = None
    last_invoice_date: date | None = None
    invoice_count: int
    reference_status: str
    next_reference_date: date | None = None
    last_reference_asked_at: date | None = None
    created_at: datetime

    # Resolved server-side so the list does not need a second round trip.
    owner_name: str | None = None
    line_count: int = 0

    # Feedback shown beside the account, not only in the feedback module.
    feedback_count: int = 0
    feedback_average: float | None = None

    # How many times this account has said no — a signal to stop asking.
    decline_count: int = 0


class CustomerFeedbackOut(BaseModel):
    id: uuid.UUID
    submitted_at_source: datetime | None = None
    overall_rating: float | None = None
    overall_comments: str | None = None
    would_recommend: str | None = None
    handled_by_name: str | None = None
    departments: list[dict] = []


class CustomerDetail(CustomerOut):
    invoice_lines: list[InvoiceLineOut] = []
    feedback: list[CustomerFeedbackOut] = []


class CustomerStats(BaseModel):
    """Header counters for the customers page, over the caller's scope."""

    total: int
    owned: int
    unowned: int
    invoice_lines: int
    invoices: int
    not_asked: int
    taken: int
    pending: int
    declined: int
    # A completed customer's open business is feedback, so this is the
    # counterpart to the reference counters above.
    with_feedback: int = 0
    awaiting_feedback: int = 0
