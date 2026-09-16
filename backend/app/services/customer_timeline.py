"""The timeline of a completed customer.

A customer from SAP is a WON deal — it has already been through the lead
pipeline. What is still open about it is the feedback and the reference, so
its history is a feedback timeline rather than a stage machine.

The view merges three sources so nothing is duplicated in storage:

  * customer_activities  — notes, calls, feedback and review requests
  * customer_references  — the reference asks, which live in their own table
  * feedback             — responses linked back to this customer

Undo is soft everywhere: the entry stays and is marked, because "this happened
and was then taken back" is the truth, and deleting it would hide a real
event from the audit trail.
"""
from __future__ import annotations

import re
import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core import authority
from app.core.authority import _All
from app.core.constants import (
    MANUAL_CUSTOMER_ACTIVITY_TYPES,
    AuditAction,
    CustomerActivityType,
    EntityType,
    ErrorCode,
)
from app.core.errors import conflict, forbidden, invalid, not_found
from app.db.base import utcnow
from app.models.customer import Customer, CustomerActivity
from app.models.feedback import Feedback
from app.models.org import User
from app.models.reference import CustomerReference
from app.services import audit
from app.services.customers import get_customer

_DIGITS = re.compile(r"\D+")


# --------------------------------------------------------- linking feedback
def normalise_mobile(value: str | None) -> str:
    """Last ten digits, so `+91 97111 22505` and `9711122505` match.

    SAP and a Google Form will never agree on formatting, and the digits are
    the only part either of them means.
    """
    digits = _DIGITS.sub("", value or "")
    return digits[-10:] if len(digits) >= 10 else digits


def match_customer(db: Session, *, mobile: str | None, email: str | None,
                   company: str | None) -> uuid.UUID | None:
    """Find the SAP customer a feedback response is about.

    Tried in order of how much each identifier actually pins down: mobile,
    then email, then an exact company-name match. A near-miss is left
    unlinked — a response attached to the wrong account is worse than one
    attached to none.
    """
    # Four columns, not whole ORM objects: this runs once per row of every
    # feedback import and Google-Form delivery, and the matching only ever
    # looks at these. The comparisons themselves are unchanged - "last ten
    # digits" has no portable SQL form, so it stays in Python.
    customers = db.execute(
        select(Customer.id, Customer.mobile, Customer.email, Customer.name)
    ).all()

    if mobile:
        target = normalise_mobile(mobile)
        if target:
            for customer_id, customer_mobile, _, _ in customers:
                if normalise_mobile(customer_mobile) == target:
                    return customer_id

    if email:
        target = email.strip().lower()
        for customer_id, _, customer_email, _ in customers:
            if (customer_email or "").strip().lower() == target:
                return customer_id

    if company:
        target = " ".join(company.split()).lower()
        for customer_id, _, _, customer_name in customers:
            if " ".join(customer_name.split()).lower() == target:
                return customer_id

    return None


def backfill_feedback_links(db: Session) -> int:
    """Link any feedback that has no customer yet. Safe to re-run."""
    linked = 0
    rows = (
        db.execute(select(Feedback).where(Feedback.customer_id.is_(None)))
        .scalars()
        .all()
    )
    for row in rows:
        matched = match_customer(
            db, mobile=row.mobile, email=row.email, company=row.company_name
        )
        if matched is not None:
            row.customer_id = matched
            linked += 1
    db.flush()
    return linked


# ------------------------------------------------------------- the timeline
def timeline(
    db: Session, actor: User, scope: set[uuid.UUID] | _All, customer_id: uuid.UUID
) -> list[dict]:
    """Everything that has happened with this customer, newest first."""
    customer = get_customer(db, actor, scope, customer_id)

    entries: list[dict] = []

    activities = (
        db.execute(
            select(CustomerActivity).where(CustomerActivity.customer_id == customer.id)
        )
        .scalars()
        .all()
    )
    references = (
        db.execute(
            select(CustomerReference).where(CustomerReference.customer_id == customer.id)
        )
        .scalars()
        .all()
    )
    responses = (
        db.execute(select(Feedback).where(Feedback.customer_id == customer.id))
        .scalars()
        .all()
    )

    actor_ids = {a.actor_user_id for a in activities if a.actor_user_id}
    actor_ids |= {r.requested_by_user_id for r in references if r.requested_by_user_id}
    names = (
        dict(db.execute(select(User.id, User.name).where(User.id.in_(actor_ids))).all())
        if actor_ids
        else {}
    )

    for activity in activities:
        entries.append(
            {
                "id": activity.id,
                "kind": "ACTIVITY",
                "activity_type": activity.activity_type,
                "title": _title_for(activity.activity_type),
                "remark": activity.remark,
                "actor_name": names.get(activity.actor_user_id),
                "created_at": activity.created_at,
                "undone_at": activity.undone_at,
                "can_undo": activity.undone_at is None,
                "related_type": activity.related_type,
                "related_id": activity.related_id,
                "rating": None,
            }
        )

    for reference in references:
        entries.append(
            {
                "id": reference.id,
                "kind": "REFERENCE",
                "activity_type": CustomerActivityType.REFERENCE_ASKED.value,
                "title": (
                    "Gave a reference"
                    if reference.outcome == "YES"
                    else "Asked for a reference — not right now"
                ),
                "remark": reference.notes
                or (
                    f"Referred {reference.referred_name or reference.referred_company}"
                    if reference.outcome == "YES"
                    else f"Ask again on {reference.next_reference_date:%d %b %Y}"
                    if reference.next_reference_date
                    else None
                ),
                "actor_name": names.get(reference.requested_by_user_id),
                "created_at": reference.created_at,
                "undone_at": None,
                # A reference is a record of an answer a customer gave. It is
                # corrected by recording a new ask, not by rubbing it out.
                "can_undo": False,
                "related_type": "REFERENCE",
                "related_id": reference.id,
                "rating": None,
            }
        )

    for response in responses:
        entries.append(
            {
                "id": response.id,
                "kind": "FEEDBACK",
                "activity_type": CustomerActivityType.FEEDBACK_RECEIVED.value,
                "title": "Feedback received",
                "remark": response.overall_comments,
                "actor_name": response.handled_by_name,
                "created_at": response.submitted_at_source or response.created_at,
                "undone_at": None,
                "can_undo": False,
                "related_type": "FEEDBACK",
                "related_id": response.id,
                "rating": float(response.overall_rating)
                if response.overall_rating is not None
                else None,
            }
        )

    entries.sort(key=lambda entry: entry["created_at"], reverse=True)
    return entries


_TITLES: dict[str, str] = {
    CustomerActivityType.NOTE: "Note added",
    CustomerActivityType.CALL: "Called",
    CustomerActivityType.WHATSAPP: "WhatsApp sent",
    CustomerActivityType.EMAIL: "Emailed",
    CustomerActivityType.MEETING: "Met",
    CustomerActivityType.FEEDBACK_REQUESTED: "Feedback requested",
    CustomerActivityType.FEEDBACK_RECEIVED: "Feedback received",
    CustomerActivityType.REVIEW_REQUESTED: "Google review requested",
    CustomerActivityType.REFERENCE_ASKED: "Reference asked",
}


def _title_for(activity_type: str) -> str:
    return _TITLES.get(activity_type, activity_type.replace("_", " ").title())


# ------------------------------------------------------------------ writes
def log(
    db: Session,
    actor: User,
    scope: set[uuid.UUID] | _All,
    customer_id: uuid.UUID,
    *,
    activity_type: str,
    remark: str | None = None,
    related_type: str | None = None,
    related_id: uuid.UUID | None = None,
) -> CustomerActivity:
    """Add one entry to a customer's timeline. Caller commits."""
    customer = get_customer(db, actor, scope, customer_id)

    if activity_type not in MANUAL_CUSTOMER_ACTIVITY_TYPES:
        raise invalid(
            f"{activity_type} is not something you can log by hand.",
            ErrorCode.VALIDATION_ERROR,
            allowed=list(MANUAL_CUSTOMER_ACTIVITY_TYPES),
        )

    activity = CustomerActivity(
        customer_id=customer.id,
        actor_user_id=actor.id,
        activity_type=str(activity_type),
        remark=remark,
        related_type=related_type,
        related_id=related_id,
    )
    db.add(activity)
    db.flush()
    return activity


def undo(
    db: Session,
    actor: User,
    scope: set[uuid.UUID] | _All,
    activity_id: uuid.UUID,
    *,
    ip_address: str | None = None,
) -> CustomerActivity:
    """Take back a timeline entry that should not have been logged."""
    activity = db.get(CustomerActivity, activity_id)
    if activity is None:
        raise not_found("Timeline entry not found.")

    # You must be able to see the customer to touch its history at all.
    get_customer(db, actor, scope, activity.customer_id)

    if activity.undone_at is not None:
        raise conflict("That entry has already been undone.")

    _require_can_undo(db, actor, activity.actor_user_id)

    activity.undone_at = utcnow()
    activity.undone_by_user_id = actor.id
    db.flush()

    audit.record(
        db,
        actor_id=actor.id,
        action=AuditAction.ACTIVITY_UNDONE,
        entity_type=EntityType.CUSTOMER,
        entity_id=activity.customer_id,
        before={"activity_type": activity.activity_type},
        after={"undone": True},
        ip_address=ip_address,
    )
    return activity


def _require_can_undo(db: Session, actor: User, author_id: uuid.UUID | None) -> None:
    """Your own entries, or those of somebody you can act on.

    Undo is a correction, not a way to rewrite what a colleague recorded.
    """
    if author_id is None or author_id == actor.id:
        return
    author = db.get(User, author_id)
    if author is None or not authority.can_act_on(db, actor, author):
        raise forbidden(
            "You can only undo your own entries, or those of your own people.",
            ErrorCode.FORBIDDEN,
        )


def feedback_for_customer(db: Session, customer_id: uuid.UUID) -> list[Feedback]:
    return list(
        db.execute(
            select(Feedback)
            .where(Feedback.customer_id == customer_id)
            .order_by(Feedback.submitted_at_source.desc())
        )
        .scalars()
        .all()
    )


def feedback_summary(db: Session, customer_ids: list[uuid.UUID]) -> dict[uuid.UUID, dict]:
    """Response count and average rating per customer, in one query.

    Used to show a feedback indicator beside a customer wherever it is listed,
    without an N+1.
    """
    if not customer_ids:
        return {}

    from sqlalchemy import func

    rows = db.execute(
        select(
            Feedback.customer_id,
            func.count(Feedback.id),
            func.avg(Feedback.overall_rating),
        )
        .where(Feedback.customer_id.in_(customer_ids))
        .group_by(Feedback.customer_id)
    ).all()

    return {
        row[0]: {
            "responses": row[1],
            "average_rating": round(float(row[2]), 2) if row[2] is not None else None,
        }
        for row in rows
    }
