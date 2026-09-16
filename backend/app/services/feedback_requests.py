"""The ask, as a record.

Before this, "we asked X for feedback" was a timeline entry and nothing more:
nothing to carry an id, nothing to change status on, nothing for a Google Form
response to point back at. This module is that record's whole lifecycle.

A request is created when somebody presses Send - never for every askable
lead or customer. "Not yet asked" stays the absence of a row, because a row
per askable record would duplicate `feedback_analysis.pending_requests` and
then drift from it.
"""
from __future__ import annotations

import secrets
import uuid
from datetime import timedelta

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.core.authority import ALL, _All
from app.core.constants import (
    OPEN_FEEDBACK_REQUEST_STATUSES,
    AuditAction,
    EntityType,
    FeedbackMatchStatus,
    FeedbackRequestStatus,
)
from app.core.errors import conflict, not_found
from app.db.base import utcnow
from app.models.customer import Customer
from app.models.feedback import FeedbackRequest
from app.models.lead import Lead
from app.models.org import User
from app.services import audit, runtime_settings

#: Long enough that guessing is hopeless, short enough to survive being
#: pasted into a URL and read back off a spreadsheet.
TOKEN_BYTES = 24


def next_reference(db: Session) -> str:
    """`FB-2026-00127`, counting from 1 each calendar year.

    Derived from the highest reference already issued this year rather than a
    separate counter table: one source of truth, and a gap after a rolled-back
    transaction is harmless - references identify, they do not audit.
    """
    year = utcnow().year
    prefix = f"FB-{year}-"
    highest = db.scalar(
        select(func.max(FeedbackRequest.reference)).where(
            FeedbackRequest.reference.like(f"{prefix}%")
        )
    )
    counter = 1
    if highest:
        try:
            counter = int(highest.rsplit("-", 1)[1]) + 1
        except (IndexError, ValueError):
            # A hand-edited reference should not stop anybody working.
            counter = 1
    return f"{prefix}{counter:05d}"


def _subject(
    db: Session, actor: User, scope: set[uuid.UUID] | _All, subject_type: str,
    subject_id: uuid.UUID,
) -> Lead | Customer:
    """The lead or customer being asked, or 404 if it is outside the scope.

    404 rather than 403 on purpose, matching every other module: a 403 would
    confirm the record exists.
    """
    if subject_type == "LEAD":
        lead = db.get(Lead, subject_id)
        if lead is None:
            raise not_found("Lead not found.")
        if scope is not ALL and lead.assigned_to_user_id not in scope:
            raise not_found("Lead not found.")
        return lead

    customer = db.get(Customer, subject_id)
    if customer is None:
        raise not_found("Customer not found.")
    if scope is not ALL and customer.owner_user_id not in scope:
        raise not_found("Customer not found.")
    return customer


def open_request_for(
    db: Session, subject_type: str, subject_id: uuid.UUID
) -> FeedbackRequest | None:
    """The outstanding ask for this record, if there is one."""
    column = (
        FeedbackRequest.lead_id if subject_type == "LEAD" else FeedbackRequest.customer_id
    )
    return (
        db.execute(
            select(FeedbackRequest)
            .where(
                column == subject_id,
                FeedbackRequest.status.in_(OPEN_FEEDBACK_REQUEST_STATUSES),
            )
            .order_by(FeedbackRequest.sent_at.desc())
        )
        .scalars()
        .first()
    )


def create(
    db: Session,
    actor: User,
    scope: set[uuid.UUID] | _All,
    *,
    subject_type: str,
    subject_id: uuid.UUID,
    channel: str | None = None,
    ip_address: str | None = None,
) -> FeedbackRequest:
    """Issue the code for a feedback ask. Does NOT mean it was sent.

    Composing has to issue the code - it is part of the link in the message -
    but the dialog can be closed, the send button is disabled when no form
    link is configured, and so on. So the request starts ISSUED, and only
    `mark_sent` (the assignee confirming it went out) makes it SENT. Recording
    SENT here is what left the queue saying "awaiting" for accounts nobody had
    messaged.

    Idempotent: composing again returns the request already open, so the code
    in a customer's inbox keeps working.
    """
    if subject_type not in ("LEAD", "CUSTOMER"):
        raise conflict(f"Unknown subject type {subject_type!r}.")

    subject = _subject(db, actor, scope, subject_type, subject_id)

    existing = open_request_for(db, subject_type, subject_id)
    if existing is not None:
        return existing

    expires_days = int(runtime_settings.get(db, "feedback.request_expires_days") or 30)
    owner_id = (
        subject.assigned_to_user_id
        if isinstance(subject, Lead)
        else subject.owner_user_id
    )

    request = FeedbackRequest(
        reference=next_reference(db),
        token=secrets.token_urlsafe(TOKEN_BYTES),
        subject_type=subject_type,
        lead_id=subject_id if subject_type == "LEAD" else None,
        customer_id=subject_id if subject_type == "CUSTOMER" else None,
        # Whose queue it belongs on. Falls back to whoever sent it, so a
        # request for an unowned account is still somebody's to chase.
        owner_user_id=owner_id or actor.id,
        status=FeedbackRequestStatus.ISSUED,
        channel=channel,
        expires_at=utcnow() + timedelta(days=expires_days),
    )
    db.add(request)
    db.flush()

    audit.record(
        db,
        actor_id=actor.id,
        action=AuditAction.FEEDBACK_REQUEST_ISSUED,
        entity_type=EntityType.FEEDBACK_REQUEST,
        entity_id=request.id,
        after={
            "reference": request.reference,
            "subject_type": subject_type,
            "subject_id": str(subject_id),
            "channel": channel,
        },
        ip_address=ip_address,
    )
    return request


def cancel(
    db: Session,
    actor: User,
    scope: set[uuid.UUID] | _All,
    request_id: uuid.UUID,
    *,
    ip_address: str | None = None,
) -> FeedbackRequest:
    """Withdraw an ask. The code stops matching from this point on."""
    request = db.get(FeedbackRequest, request_id)
    if request is None:
        raise not_found("Feedback request not found.")
    if not can_see(request, scope):
        raise not_found("Feedback request not found.")
    if request.status not in OPEN_FEEDBACK_REQUEST_STATUSES:
        raise conflict(f"That request is already {request.status.lower()}.")

    previous = request.status
    request.status = FeedbackRequestStatus.CANCELLED
    db.flush()

    audit.record(
        db,
        actor_id=actor.id,
        action=AuditAction.FEEDBACK_REQUEST_CANCELLED,
        entity_type=EntityType.FEEDBACK_REQUEST,
        entity_id=request.id,
        before={"status": previous},
        after={"status": FeedbackRequestStatus.CANCELLED},
        ip_address=ip_address,
    )
    return request


def can_see(request: FeedbackRequest, scope: set[uuid.UUID] | _All) -> bool:
    """Requests follow the people scope, like leads and customers do."""
    if scope is ALL:
        return True
    return request.owner_user_id in scope


def find_by_code(db: Session, code: str | None) -> FeedbackRequest | None:
    """Resolve `FB-2026-00127.k7x9m2…` back to the ask.

    The token is what is trusted; the reference is checked against the same
    row so a mismatched pair is treated as no code at all rather than as a
    match. A guessable reference alone would let a stranger attach a response
    to somebody else's account.
    """
    if not code:
        return None

    raw = code.strip()
    reference, _, token = raw.partition(".")
    if not token:
        # Someone sent only half of it. A bare reference is guessable, so it
        # is not enough on its own.
        return None

    request = (
        db.execute(select(FeedbackRequest).where(FeedbackRequest.token == token.strip()))
        .scalars()
        .first()
    )
    if request is None:
        return None
    if request.reference != reference.strip():
        return None
    if request.status == FeedbackRequestStatus.CANCELLED:
        # Withdrawn. The response is still kept, just not attached.
        return None
    return request


def mark_sent(
    db: Session,
    actor: User,
    request: FeedbackRequest,
    *,
    channel: str | None = None,
    ip_address: str | None = None,
) -> FeedbackRequest:
    """The assignee confirmed the message went out. ISSUED -> SENT.

    `sent_at` moves to now: the clock that matters for "how long have we been
    waiting" starts when it was sent, not when somebody first opened a draft.
    Idempotent - confirming twice changes nothing.
    """
    if request.status == FeedbackRequestStatus.SENT:
        return request
    if request.status != FeedbackRequestStatus.ISSUED:
        raise conflict(f"That request is already {request.status.lower()}.")

    request.status = FeedbackRequestStatus.SENT
    request.sent_at = utcnow()
    if channel:
        request.channel = channel
    db.flush()

    audit.record(
        db,
        actor_id=actor.id,
        action=AuditAction.FEEDBACK_REQUEST_SENT,
        entity_type=EntityType.FEEDBACK_REQUEST,
        entity_id=request.id,
        before={"status": FeedbackRequestStatus.ISSUED},
        after={"status": FeedbackRequestStatus.SENT, "channel": request.channel},
        ip_address=ip_address,
    )
    return request


#: How a form column carrying the request code is recognised. The company owns
#: the question wording, so this matches by content, like every other column.
REFERENCE_PHRASES = ("reference code", "reference id", "reference", "request code")


def reference_header(headers: list[str]) -> str | None:
    """The column carrying the request code, if the form has one."""
    for header in headers:
        lowered = " ".join((header or "").split()).lower()
        if any(phrase in lowered for phrase in REFERENCE_PHRASES):
            return header
    return None


def match_response(
    db: Session,
    *,
    code: str | None,
    mobile: str | None,
    email: str | None,
) -> tuple[FeedbackRequest | None, str | None]:
    """Which ask does this response answer? ONE rule, for every way in.

    The webhook and the file importer used to match differently: the webhook
    trusted the code and then fell back to the archived SAP customer book; the
    importer never looked at the code at all. So a response to a lead's ask
    could only ever complete it through one door, and an un-coded response
    could only ever land on an archived SAP customer - never on the lead the
    portal actually asked.

    In order of how much each identifier pins down:

      1. the request code      exact and unguessable      -> MATCHED_TOKEN
      2. mobile, then email    against LEADS with an open  -> MATCHED_CONTACT
                               ask, and only if exactly one
                               lead matches
      3. nothing               caller decides (archive fallback, or UNMATCHED)

    Step 2 only considers leads that were actually asked. A response is never
    attached to a lead nobody asked - that would invent an ask that did not
    happen - and never to one of two leads sharing a phone number.
    """
    request = find_by_code(db, code)
    if request is not None:
        return request, FeedbackMatchStatus.MATCHED_TOKEN

    from app.services.customer_timeline import normalise_mobile

    open_asks = list(
        db.execute(
            select(FeedbackRequest).where(
                FeedbackRequest.subject_type == "LEAD",
                FeedbackRequest.status.in_(OPEN_FEEDBACK_REQUEST_STATUSES),
            )
        ).scalars()
    )
    if not open_asks:
        return None, None
    leads = {
        lead.id: lead
        for lead in db.execute(
            select(Lead).where(Lead.id.in_({r.lead_id for r in open_asks}))
        ).scalars()
    }

    def unique(predicate) -> FeedbackRequest | None:
        hits = {r.lead_id: r for r in open_asks if r.lead_id in leads and predicate(leads[r.lead_id])}
        return next(iter(hits.values())) if len(hits) == 1 else None

    target = normalise_mobile(mobile) if mobile else ""
    if target:
        hit = unique(lambda lead: normalise_mobile(lead.mobile) == target)
        if hit is not None:
            return hit, FeedbackMatchStatus.MATCHED_CONTACT

    wanted = (email or "").strip().lower()
    if wanted:
        hit = unique(lambda lead: (lead.email or "").strip().lower() == wanted)
        if hit is not None:
            return hit, FeedbackMatchStatus.MATCHED_CONTACT

    return None, None


def open_lead_requests(db: Session, limit: int = 200) -> list[FeedbackRequest]:
    """Leads that were asked and have not answered - for resolving by hand."""
    return list(
        db.execute(
            select(FeedbackRequest)
            .where(
                FeedbackRequest.subject_type == "LEAD",
                FeedbackRequest.status.in_(OPEN_FEEDBACK_REQUEST_STATUSES),
            )
            .order_by(FeedbackRequest.sent_at.desc())
            .limit(limit)
        ).scalars()
    )


def mark_completed(db: Session, request: FeedbackRequest) -> None:
    """A response arrived. Idempotent - a second one does not re-complete."""
    if request.status == FeedbackRequestStatus.COMPLETED:
        return
    request.status = FeedbackRequestStatus.COMPLETED
    request.completed_at = utcnow()
    db.flush()


def expire_due(db: Session) -> int:
    """Age out unanswered asks so the response rate has a fixed denominator.

    Safe to run repeatedly; only touches SENT rows whose date has passed.
    """
    now = utcnow()
    rows = (
        db.execute(
            select(FeedbackRequest).where(
                FeedbackRequest.status == FeedbackRequestStatus.SENT,
                FeedbackRequest.expires_at.is_not(None),
                FeedbackRequest.expires_at < now,
            )
        )
        .scalars()
        .all()
    )
    for request in rows:
        request.status = FeedbackRequestStatus.EXPIRED
    if rows:
        db.flush()
    return len(rows)


def for_subjects(
    db: Session, lead_ids: set[uuid.UUID], customer_ids: set[uuid.UUID]
) -> dict[tuple[str, uuid.UUID], FeedbackRequest]:
    """The latest request per subject, for decorating a list in one query."""
    if not lead_ids and not customer_ids:
        return {}

    clauses = []
    if lead_ids:
        clauses.append(FeedbackRequest.lead_id.in_(lead_ids))
    if customer_ids:
        clauses.append(FeedbackRequest.customer_id.in_(customer_ids))

    stmt = select(FeedbackRequest).where(or_(*clauses))

    # Ascending, so a later request overwrites an earlier one and the dict
    # ends up holding the most recent per subject.
    out: dict[tuple[str, uuid.UUID], FeedbackRequest] = {}
    for request in db.execute(stmt.order_by(FeedbackRequest.sent_at)).scalars():
        subject_id = request.lead_id or request.customer_id
        if subject_id is not None:
            out[(request.subject_type, subject_id)] = request
    return out


def scoped(
    db: Session, actor: User, scope: set[uuid.UUID] | _All, *, status: str | None = None
) -> list[FeedbackRequest]:
    """Requests this person may see, newest first."""
    stmt = select(FeedbackRequest)
    if not isinstance(scope, _All):
        stmt = stmt.where(
            FeedbackRequest.owner_user_id.in_(scope)
            if scope
            else FeedbackRequest.id.in_([])
        )
    if status:
        stmt = stmt.where(FeedbackRequest.status == status)
    return list(db.execute(stmt.order_by(FeedbackRequest.sent_at.desc())).scalars())


def require_visible(
    db: Session, actor: User, scope: set[uuid.UUID] | _All, request_id: uuid.UUID
) -> FeedbackRequest:
    request = db.get(FeedbackRequest, request_id)
    if request is None or not can_see(request, scope):
        raise not_found("Feedback request not found.")
    return request


__all__ = [
    "can_see",
    "cancel",
    "create",
    "expire_due",
    "find_by_code",
    "for_subjects",
    "mark_completed",
    "next_reference",
    "open_request_for",
    "require_visible",
    "scoped",
]
