"""The converted-customer book."""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Query

from app.core.constants import ReferenceStatus
from app.core.deps import DbSession, SuperAdminUser, VisibilityScope
from app.schemas.common import MAX_PAGE, MAX_PAGE_SIZE, Page
from sqlalchemy import select

from app.models.org import Department
from app.schemas.customer import (
    CustomerDetail,
    CustomerFeedbackOut,
    CustomerOut,
    CustomerStats,
    InvoiceLineOut,
)
from app.services import customer_timeline
from app.services import customers as customer_service
from app.services import references as reference_service

router = APIRouter(prefix="/customers", tags=["customers"])


def _to_out(
    customer,
    owner_lookup: dict,
    feedback_lookup: dict | None = None,
    decline_lookup: dict | None = None,
) -> CustomerOut:
    out = CustomerOut.model_validate(customer)
    out.owner_name = owner_lookup.get(customer.owner_user_id)
    out.line_count = len(customer.invoice_lines)

    summary = (feedback_lookup or {}).get(customer.id)
    if summary:
        out.feedback_count = summary["responses"]
        out.feedback_average = summary["average_rating"]
    out.decline_count = (decline_lookup or {}).get(customer.id, 0)
    return out


# ---------------------------------------------------------------------------
# Browsing the SAP book - the account list, one account's page, and the
# counters above them - is Super Admin only.
#
# What is NOT restricted, deliberately: the customer TIMELINE and the message
# routes below. Those are how a BDE asks their own account for feedback, and
# how Reference Tracking records an ask. Locking them would empty two modules
# for everyone but one person, which is the opposite of what was asked for.
# They were already narrowed by `VisibilityScope` to the caller's own accounts.
# ---------------------------------------------------------------------------
@router.get("/stats", response_model=CustomerStats)
def customer_stats(
    actor: SuperAdminUser, db: DbSession, scope: VisibilityScope
) -> CustomerStats:
    return CustomerStats(**customer_service.stats(db, actor, scope))


@router.get("", response_model=Page[CustomerOut])
def list_customers(
    actor: SuperAdminUser,
    db: DbSession,
    scope: VisibilityScope,
    search: str | None = Query(default=None, max_length=120),
    reference_status: ReferenceStatus | None = Query(default=None),
    owner_id: uuid.UUID | None = Query(default=None),
    unowned_only: bool = Query(default=False),
    page: int = Query(default=1, ge=1, le=MAX_PAGE),
    page_size: int = Query(default=25, ge=1, le=MAX_PAGE_SIZE),
) -> Page[CustomerOut]:
    rows, total = customer_service.list_customers(
        db,
        actor,
        scope,
        search=search,
        reference_status=str(reference_status) if reference_status else None,
        owner_id=owner_id,
        unowned_only=unowned_only,
        page=page,
        page_size=page_size,
    )
    lookup = customer_service.owner_names(db, rows)
    feedback_lookup = customer_timeline.feedback_summary(db, [c.id for c in rows])
    decline_lookup = reference_service.decline_counts(db, [c.id for c in rows])
    return Page[CustomerOut](
        items=[_to_out(c, lookup, feedback_lookup, decline_lookup) for c in rows],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get("/{customer_id}", response_model=CustomerDetail)
def get_customer(
    customer_id: uuid.UUID,
    actor: SuperAdminUser,
    db: DbSession,
    scope: VisibilityScope,
) -> CustomerDetail:
    customer = customer_service.get_customer(db, actor, scope, customer_id)
    lookup = customer_service.owner_names(db, [customer])
    feedback_lookup = customer_timeline.feedback_summary(db, [customer.id])

    detail = CustomerDetail.model_validate(customer)
    detail.owner_name = lookup.get(customer.owner_user_id)
    detail.line_count = len(customer.invoice_lines)
    detail.decline_count = reference_service.decline_counts(db, [customer.id]).get(
        customer.id, 0
    )

    summary = feedback_lookup.get(customer.id)
    if summary:
        detail.feedback_count = summary["responses"]
        detail.feedback_average = summary["average_rating"]

    # The responses themselves, so a completed customer's page answers "what
    # did they say?" without a trip to the feedback module.
    department_names = dict(db.execute(select(Department.id, Department.name)).all())
    detail.feedback = [
        CustomerFeedbackOut(
            id=response.id,
            submitted_at_source=response.submitted_at_source,
            overall_rating=float(response.overall_rating)
            if response.overall_rating is not None
            else None,
            overall_comments=response.overall_comments,
            would_recommend=response.would_recommend,
            handled_by_name=response.handled_by_name,
            departments=[
                {
                    "name": department_names.get(rating.department_id, "—"),
                    "rating": float(rating.rating) if rating.rating is not None else None,
                    "comments": rating.comments,
                }
                for rating in response.department_ratings
            ],
        )
        for response in customer_timeline.feedback_for_customer(db, customer.id)
    ]
    detail.invoice_lines = [
        InvoiceLineOut.model_validate(line)
        for line in sorted(
            customer.invoice_lines,
            key=lambda line: (line.invoice_date is None, line.invoice_date, line.invoice_no),
            reverse=True,
        )
    ]
    return detail
