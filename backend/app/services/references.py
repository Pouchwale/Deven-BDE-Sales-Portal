"""Reference tracking.

Recording an ask does two things in one transaction: it writes the reference
row (the history) and it moves the customer's roll-up status (the working
state). Letting those drift apart is how a follow-up queue starts lying.
"""
from __future__ import annotations

import uuid
from datetime import date

from sqlalchemy import Select, func, or_, select
from sqlalchemy.orm import Session

from app.core import authority
from app.core.authority import ALL, _All
from app.core.constants import (
    COMPLETED_REFERENCE_OUTCOMES,
    AuditAction,
    EntityType,
    ErrorCode,
    LeadStatus,
    ReferenceOutcome,
    ReferenceStatus,
)
from app.core.errors import conflict, forbidden, invalid
from app.services import metrics
from app.models.customer import Customer
from app.models.lead import Lead
from app.models.org import User
from app.models.reference import CustomerReference
from app.services import audit
from app.services import leads as lead_service
from app.services.customers import get_customer


def _scoped(scope: set[uuid.UUID] | _All) -> Select:
    stmt = select(CustomerReference)
    if scope is ALL:
        return stmt
    if not scope:
        return stmt.where(CustomerReference.requested_by_user_id.in_([]))
    return stmt.where(CustomerReference.requested_by_user_id.in_(scope))


def _resolve_attribution(
    db: Session, actor: User, requested_by_user_id: uuid.UUID | None
) -> uuid.UUID:
    """Whose reference this is.

    Defaults to the caller. Crediting somebody else is an act on them, so a
    manager can credit their own people and nobody else's.
    """
    if requested_by_user_id is None or requested_by_user_id == actor.id:
        return actor.id

    target = db.get(User, requested_by_user_id)
    if target is None:
        raise invalid("That person does not exist.")
    if not authority.can_act_on(db, actor, target):
        raise forbidden(f"You cannot credit a reference to {target.name}.")
    return target.id


def _askable_lead(db: Session, scope: set[uuid.UUID] | _All, lead_id: uuid.UUID) -> Lead:
    """A lead you may ask for a reference: converted, and in your scope.

    A prospect still in the pipeline has not bought anything yet, so there is
    nothing to refer us on the strength of.
    """
    lead = lead_service.get_lead(db, scope, lead_id)
    if lead.status != LeadStatus.CONVERTED:
        raise invalid(
            "Only a converted lead can be asked for a reference.",
            ErrorCode.VALIDATION_ERROR,
        )
    # Won is not the same as delivered. An account becomes askable ten days
    # after the post-sale sheet says it was invoiced - see core/eligibility.py
    # and services/metrics.py. Checked HERE as well as in the queue, so the
    # rule cannot be walked around by posting a lead id directly.
    # The same date the counts use, from the same helper - so the guard can
    # never refuse an account the queue is showing as askable, or the reverse.
    ready_on = metrics.reference_ready_dates(db, [lead.id]).get(lead.id)
    if ready_on is None:
        raise invalid(
            "This deal is won but nothing has been synced for it yet. It can "
            "be asked once the post-sale data arrives.",
            ErrorCode.VALIDATION_ERROR,
        )
    if ready_on > date.today():
        raise invalid(
            f"Its reference date is {ready_on:%d %b %Y}. It can be asked from "
            f"then.",
            ErrorCode.VALIDATION_ERROR,
        )
    return lead


def record(
    db: Session,
    actor: User,
    scope: set[uuid.UUID] | _All,
    *,
    customer_id: uuid.UUID | None = None,
    lead_id: uuid.UUID | None = None,
    outcome: str,
    asked_on: date | None = None,
    next_reference_date: date | None = None,
    referred_name: str | None = None,
    referred_company: str | None = None,
    referred_mobile: str | None = None,
    referred_email: str | None = None,
    notes: str | None = None,
    requested_by_user_id: uuid.UUID | None = None,
    ip_address: str | None = None,
) -> CustomerReference:
    """Record one ask against a won account. Caller commits.

    The subject is either a SAP customer or a converted lead — exactly one,
    matching ck_customer_references_subject. Both are won deals; they simply
    live in different tables.
    """
    if (customer_id is None) == (lead_id is None):
        raise invalid(
            "An ask belongs to exactly one account — a customer or a converted lead.",
            ErrorCode.VALIDATION_ERROR,
        )

    # Whichever it is, it carries the same three roll-up fields.
    subject = (
        get_customer(db, actor, scope, customer_id)
        if customer_id is not None
        else _askable_lead(db, scope, lead_id)  # type: ignore[arg-type]
    )
    # A finished conversation stays finished - enforced HERE, not just by the
    # buttons the UI chooses to show.
    #
    # The roll-up below is last-write-wins, so without this an API call could
    # record "not right now" against an account that already gave a
    # reference: it dropped back into the follow-up queue AND vanished from
    # references received, because the KPI counts accounts in TAKEN. The UI
    # never offered that action, which is exactly why nobody noticed.
    #
    #   DECLINED ("not shared")  nothing more to ask.
    #   TAKEN                    only another YES - that is the "+", a second
    #                            person referred by the same customer.
    if subject.reference_status == ReferenceStatus.DECLINED:
        raise conflict(
            f"{subject.name} already said they have nobody to refer. "
            "That ask is complete.",
            ErrorCode.CONFLICT,
            reference_status=subject.reference_status,
        )
    if subject.reference_status == ReferenceStatus.TAKEN and outcome != ReferenceOutcome.YES:
        raise conflict(
            f"{subject.name} already gave a reference. You can record another "
            "referral, but the ask cannot be reopened.",
            ErrorCode.CONFLICT,
            reference_status=subject.reference_status,
        )

    credited_to = _resolve_attribution(db, actor, requested_by_user_id)
    asked = asked_on or date.today()

    if asked > date.today():
        raise invalid("An ask cannot be recorded in the future.")

    # "Not right now" is the only answer that owes a follow-up, and the
    # database says so too (ck_customer_references_followup). Checked here so
    # the person gets a sentence rather than a constraint violation.
    if outcome == ReferenceOutcome.NO and next_reference_date is None:
        raise invalid(
            "Choose when to ask again, or record it as not shared.",
            ErrorCode.VALIDATION_ERROR,
        )
    # A finished conversation carries no follow-up date. Dropping it rather
    # than refusing: the caller said the conversation is over, and a stray
    # date in the payload is not worth an error.
    if outcome in COMPLETED_REFERENCE_OUTCOMES:
        next_reference_date = None

    reference = CustomerReference(
        customer_id=customer_id,
        lead_id=lead_id,
        requested_by_user_id=credited_to,
        outcome=str(outcome),
        asked_on=asked,
        next_reference_date=next_reference_date,
        referred_name=referred_name,
        referred_company=referred_company,
        referred_mobile=referred_mobile,
        referred_email=referred_email,
        notes=notes,
    )
    db.add(reference)

    # The account's roll-up moves with the history, in the same transaction.
    subject.last_reference_asked_at = asked
    if outcome == ReferenceOutcome.YES:
        # They gave one. Done, and a reference was actually received.
        subject.reference_status = ReferenceStatus.TAKEN
        subject.next_reference_date = None
    elif outcome == ReferenceOutcome.NOT_SHARED:
        # Asked and answered: no reference, and nothing to chase. Done, but
        # NOT counted as a reference received - see `stats`, which reports
        # "completed" and "received" as two different numbers on purpose.
        subject.reference_status = ReferenceStatus.DECLINED
        subject.next_reference_date = None
    else:
        # "Not right now." Still open; comes back on the follow-up date.
        subject.reference_status = ReferenceStatus.PENDING
        subject.next_reference_date = next_reference_date

    db.flush()

    audit.record(
        db,
        actor_id=actor.id,
        action=AuditAction.REFERENCE_RECORDED,
        entity_type=EntityType.REFERENCE,
        entity_id=reference.id,
        after={
            "customer_id": customer_id,
            "lead_id": lead_id,
            "outcome": str(outcome),
            "requested_by_user_id": credited_to,
            "next_reference_date": (
                next_reference_date.isoformat() if next_reference_date else None
            ),
        },
        ip_address=ip_address,
    )
    return reference


def list_references(
    db: Session,
    scope: set[uuid.UUID] | _All,
    *,
    customer_id: uuid.UUID | None = None,
    outcome: str | None = None,
    search: str | None = None,
    page: int = 1,
    page_size: int = 25,
) -> tuple[list[CustomerReference], int]:
    stmt = _scoped(scope)
    if customer_id is not None:
        stmt = stmt.where(CustomerReference.customer_id == customer_id)
    if outcome:
        stmt = stmt.where(CustomerReference.outcome == outcome)
    if search:
        term = search.strip().lower()
        stmt = stmt.where(
            or_(
                func.lower(func.coalesce(CustomerReference.referred_name, "")).contains(term),
                func.lower(func.coalesce(CustomerReference.referred_company, "")).contains(term),
                func.lower(func.coalesce(CustomerReference.referred_mobile, "")).contains(term),
            )
        )

    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = (
        db.execute(
            stmt.order_by(CustomerReference.asked_on.desc(), CustomerReference.created_at.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        .scalars()
        .all()
    )
    return list(rows), total


def decorate(db: Session, references: list[CustomerReference]) -> dict[str, dict]:
    """Source-account and creditee names for a page of references.

    An ask points at a customer or at a converted lead, so both get resolved —
    a row that could not name who gave the reference would be useless.
    """
    customer_ids = {r.customer_id for r in references if r.customer_id}
    lead_ids = {r.lead_id for r in references if r.lead_id}
    user_ids = {r.requested_by_user_id for r in references if r.requested_by_user_id}

    leads = (
        dict(db.execute(select(Lead.id, Lead.name).where(Lead.id.in_(lead_ids))).all())
        if lead_ids
        else {}
    )

    customers = (
        dict(
            db.execute(
                select(Customer.id, Customer.name).where(Customer.id.in_(customer_ids))
            ).all()
        )
        if customer_ids
        else {}
    )
    codes = (
        dict(
            db.execute(
                select(Customer.id, Customer.sap_code).where(Customer.id.in_(customer_ids))
            ).all()
        )
        if customer_ids
        else {}
    )
    users = (
        dict(db.execute(select(User.id, User.name).where(User.id.in_(user_ids))).all())
        if user_ids
        else {}
    )
    return {"customers": customers, "codes": codes, "users": users, "leads": leads}


# ------------------------------------------------------------ follow-ups
def decline_counts(db: Session, customer_ids: list[uuid.UUID]) -> dict[uuid.UUID, int]:
    """How many times each customer has said no.

    A customer racking up declines is a signal to stop asking, not a data
    point to bury in the history.
    """
    if not customer_ids:
        return {}
    rows = db.execute(
        select(CustomerReference.customer_id, func.count(CustomerReference.id))
        .where(
            CustomerReference.customer_id.in_(customer_ids),
            CustomerReference.outcome == ReferenceOutcome.NO,
        )
        .group_by(CustomerReference.customer_id)
    ).all()
    return {row[0]: row[1] for row in rows}


def lead_decline_counts(db: Session, lead_ids: list[uuid.UUID]) -> dict[uuid.UUID, int]:
    """The same count, for the other kind of account."""
    if not lead_ids:
        return {}
    rows = db.execute(
        select(CustomerReference.lead_id, func.count(CustomerReference.id))
        .where(
            CustomerReference.lead_id.in_(lead_ids),
            CustomerReference.outcome == ReferenceOutcome.NO,
        )
        .group_by(CustomerReference.lead_id)
    ).all()
    return {row[0]: row[1] for row in rows}


def askable_accounts(
    db: Session,
    actor: User,
    scope: set[uuid.UUID] | _All,
    *,
    reference_status: str | None = None,
    search: str | None = None,
    owner_id: uuid.UUID | None = None,
) -> list[dict]:
    """Every won account that can be asked for a reference.

    ONE source: converted portal leads whose post-sale record says they were
    invoiced at least ten days ago. Not the SAP customer book - that was a
    separate universe with no link to any lead, which is how this module could
    report 17 accounts while Assigned Leads reported 22 conversions and both
    were right about different things.

    An account with no post-sale record yet is not listed, because nobody can
    ask it anything until the sheet says it was delivered. The COUNT of those
    is reported separately (`metrics.post_sale_population`) so the module can
    say "12 waiting on the next sync" rather than showing a bare zero.
    """
    population = metrics.post_sale_population(db, scope)
    eligible_ids = population["eligible_lead_ids"]
    if not eligible_ids:
        return []

    stmt = select(Lead).where(Lead.id.in_(eligible_ids))
    if reference_status:
        stmt = stmt.where(Lead.reference_status == reference_status)
    if owner_id is not None:
        stmt = stmt.where(Lead.assigned_to_user_id == owner_id)
    if search:
        term = search.strip().lower()
        stmt = stmt.where(
            func.lower(Lead.name).contains(term)
            | func.lower(func.coalesce(Lead.company_name, "")).contains(term)
        )
    leads = list(db.execute(stmt.order_by(Lead.name)).scalars().unique().all())
    if not leads:
        return []

    lead_declines = lead_decline_counts(db, [lead.id for lead in leads])
    owner_ids = {lead.assigned_to_user_id for lead in leads if lead.assigned_to_user_id}
    owners = (
        dict(db.execute(select(User.id, User.name).where(User.id.in_(owner_ids))).all())
        if owner_ids
        else {}
    )
    # The sheet's own Reference Date, so a row says WHY it is askable in the
    # business's own terms rather than asking the reader to take it on trust.
    ready = metrics.reference_ready_dates(db, [lead.id for lead in leads])

    rows = [
        {
            "subject_type": "LEAD",
            "subject_id": lead.id,
            "subject_name": lead.name,
            "company_name": lead.company_name,
            "mobile": lead.mobile,
            "email": lead.email,
            "owner_user_id": lead.assigned_to_user_id,
            "owner_name": owners.get(lead.assigned_to_user_id),
            "reference_status": lead.reference_status,
            "last_reference_asked_at": lead.last_reference_asked_at,
            "next_reference_date": lead.next_reference_date,
            "decline_count": lead_declines.get(lead.id, 0),
            "reference_date": ready.get(lead.id),
            "converted_at": lead.closed_at,
        }
        for lead in leads
    ]
    rows.sort(key=lambda row: row["subject_name"].lower())
    return rows


def follow_ups_due(
    db: Session,
    actor: User,
    scope: set[uuid.UUID] | _All,
    *,
    on: date | None = None,
    include_future: bool = False,
    group_by: str = "due_date",
) -> list[dict]:
    """Won accounts whose "ask me later" date has arrived.

    Converted leads only, matching `askable_accounts`. A follow-up queue built
    from a different population than the ask queue is how the two end up
    disagreeing about who still owes an answer.

    Still not lead *pipeline* rows: a follow-up is surfaced as a work item and
    never inflates the lead counts.
    """
    today = on or date.today()

    stmt = select(Lead).where(
        Lead.status == LeadStatus.CONVERTED,
        Lead.reference_status == ReferenceStatus.PENDING,
        Lead.next_reference_date.is_not(None),
    )
    if scope is not ALL:
        stmt = stmt.where(
            Lead.assigned_to_user_id.in_(scope) if scope else Lead.id.in_([])
        )
    if not include_future:
        stmt = stmt.where(Lead.next_reference_date <= today)

    leads = list(db.execute(stmt.order_by(Lead.next_reference_date)).scalars().all())
    if not leads:
        return []

    lead_declines = lead_decline_counts(db, [lead.id for lead in leads])
    owner_ids = {lead.assigned_to_user_id for lead in leads if lead.assigned_to_user_id}
    owners = (
        dict(db.execute(select(User.id, User.name).where(User.id.in_(owner_ids))).all())
        if owner_ids
        else {}
    )

    due = [
        {
            "subject_type": "LEAD",
            "subject_id": lead.id,
            "subject_name": lead.name,
            "company_name": lead.company_name,
            "mobile": lead.mobile,
            "email": lead.email,
            "owner_user_id": lead.assigned_to_user_id,
            "owner_name": owners.get(lead.assigned_to_user_id),
            "next_reference_date": lead.next_reference_date,
            "last_reference_asked_at": lead.last_reference_asked_at,
            "days_overdue": max(0, (today - lead.next_reference_date).days),
            "decline_count": lead_declines.get(lead.id, 0),
        }
        for lead in leads
        if lead.next_reference_date is not None
    ]

    if group_by == "owner":
        due.sort(key=lambda row: (row["owner_name"] or "", row["next_reference_date"]))
    else:
        due.sort(key=lambda row: row["next_reference_date"])
    return due

def stats(db: Session, actor: User, scope: set[uuid.UUID] | _All) -> dict:
    """The reference KPIs, all from one population.

    Every number here is derived by `services.metrics` from the SAME set of
    eligible converted leads, so the module, the dashboard and the assistant
    cannot disagree. Before this, the denominator came from the SAP customer
    book while the numerator came from lead reference states - two universes
    with no row in common, which is exactly why they never reconciled.

    `awaiting_sync` is reported alongside so the UI can explain a small
    eligible count rather than showing a bare zero: "22 won, 12 waiting on the
    next sync" is a different situation from "no customers".
    """
    reference = metrics.reference_metrics(db, scope)
    due = len(follow_ups_due(db, actor, scope))

    return {
        # The won book, and how much of it is actionable yet.
        "converted_leads": reference["converted"],
        "eligible_accounts": reference["eligible"],
        "awaiting_sync": reference["awaiting_sync"],
        "waiting_period": reference["waiting"],
        # Two different numbers, deliberately not collapsed: a customer who
        # had nobody to refer COMPLETED the request without producing a
        # reference. Reporting both as "references taken" would claim
        # referrals the company never received.
        "references_taken": reference["taken"],
        "requests_completed": reference["completed"],
        "references_pending": reference["pending"],
        "references_declined": reference["declined"],
        "not_asked": reference["not_asked"],
        "follow_ups_due": due,
        "completion_rate": reference["completion_rate"],
        "reference_rate": reference["reference_rate"],
        # The agreed score: -(accounts still owing a reference / eligible) %.
        "reference_score": reference["score"],
    }
