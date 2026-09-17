"""Assigned leads — Module 2."""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Query, Request, status

from sqlalchemy.orm import Session

from app.core.constants import LeadActivityType, LeadOrigin, LeadPriority, LeadStatus
from app.core.deps import (
    AdminUser,
    CurrentUser,
    DbSession,
    LeadershipUser,
    VisibilityScope,
    client_ip,
)
from app.core.errors import invalid
from app.schemas.common import MAX_PAGE, MAX_PAGE_SIZE, Page
from app.schemas.lead import (
    LeadActivityCreate,
    LeadActivityOut,
    LeadCreate,
    LeadDetail,
    LeadOut,
    LeadStats,
    LeadStatusChange,
    LeadUpdate,
)
from app.schemas.messaging import ComposedMessageOut, SentBody
from app.services import leads as lead_service
from app.services import feedback_requests
from app.services import messaging

# Creating and assigning work requires MANAGER or above (plan v3 s7.3), which
# is what LeadershipUser resolves to.
router = APIRouter(prefix="/leads", tags=["leads"])


def _reassignment_count(lead) -> int:
    return sum(1 for a in lead.activities if a.activity_type == "REASSIGNED")


def _to_out(lead, names: dict) -> LeadOut:
    out = LeadOut.model_validate(lead)
    out.assigned_to_name = names.get(lead.assigned_to_user_id)
    out.assigned_by_name = names.get(lead.assigned_by_user_id)
    out.activity_count = len(lead.activities)
    out.last_activity_at = (
        max(activity.created_at for activity in lead.activities)
        if lead.activities
        else None
    )
    out.reassignment_count = _reassignment_count(lead)
    return out


@router.get("/stats", response_model=LeadStats)
def lead_stats(_: CurrentUser, db: DbSession, scope: VisibilityScope) -> LeadStats:
    return LeadStats(**lead_service.stats(db, scope))


@router.get("", response_model=Page[LeadOut])
def list_leads(
    actor: CurrentUser,
    db: DbSession,
    scope: VisibilityScope,
    status_filter: LeadStatus | None = Query(default=None, alias="status"),
    origin: LeadOrigin | None = Query(default=None),
    assigned_to: uuid.UUID | None = Query(default=None),
    assigned_by_me: bool = Query(
        default=False,
        description="Only leads the authenticated caller assigned.",
    ),
    priority: LeadPriority | None = Query(default=None),
    open_only: bool = Query(default=False),
    search: str | None = Query(default=None, max_length=120),
    page: int = Query(default=1, ge=1, le=MAX_PAGE),
    page_size: int = Query(default=25, ge=1, le=MAX_PAGE_SIZE),
) -> Page[LeadOut]:
    """Leads within the caller's visibility.

    `assigned_by_me` is a FLAG, not a user id. It resolves to the token's own
    subject on the server, so "assigned by me" cannot be turned into "assigned
    by whoever I care to name" by editing a query string.
    """
    rows, total = lead_service.list_leads(
        db,
        scope,
        status=str(status_filter) if status_filter else None,
        origin=str(origin) if origin else None,
        assigned_to=assigned_to,
        assigned_by=actor.id if assigned_by_me else None,
        priority=str(priority) if priority else None,
        open_only=open_only,
        search=search,
        page=page,
        page_size=page_size,
    )
    names = lead_service.decorate(db, rows)
    return Page[LeadOut](
        items=[_to_out(lead, names) for lead in rows],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get("/assigned-by-me/counts", response_model=list[dict])
def assigned_by_me_counts(
    actor: CurrentUser, db: DbSession, scope: VisibilityScope
) -> list[dict]:
    """How many leads the caller assigned, per assignee.

    Feeds the filter chips. Counted in SQL so a chip can show a number for
    somebody whose leads are not on the page being looked at.
    """
    return lead_service.assigned_by_counts(db, scope, actor.id)


@router.post("", response_model=LeadDetail, status_code=status.HTTP_201_CREATED)
def create_lead(
    payload: LeadCreate,
    request: Request,
    actor: LeadershipUser,
    db: DbSession,
    scope: VisibilityScope,
) -> LeadDetail:
    lead = lead_service.create_lead(
        db,
        actor,
        name=payload.name,
        assigned_to_user_id=payload.assigned_to_user_id,
        company_name=payload.company_name,
        mobile=payload.mobile,
        email=payload.email,
        city=payload.city,
        requirement=payload.requirement,
        priority=str(payload.priority),
        next_follow_up_date=payload.next_follow_up_date,
        ip_address=client_ip(request),
    )
    db.commit()
    return _detail(db, lead_service.get_lead(db, scope, lead.id))


@router.post("/{lead_id}/reopen", response_model=LeadDetail)
def reopen_lead(
    lead_id: uuid.UUID,
    payload: LeadStatusChange,
    request: Request,
    actor: AdminUser,
    db: DbSession,
    scope: VisibilityScope,
) -> LeadDetail:
    """Put a lead closed by mistake back at the start. Administrators only.

    The narrow replacement for Undo: it cannot rewind to an arbitrary point,
    it needs a reason, and it appears on the timeline as its own event.
    """
    lead = lead_service.get_lead(db, scope, lead_id)
    lead_service.reopen(
        db, actor, lead, remark=payload.remark or "", ip_address=client_ip(request)
    )
    db.commit()
    return _detail(db, lead_service.get_lead(db, scope, lead_id))


@router.get("/{lead_id}", response_model=LeadDetail)
def get_lead(
    lead_id: uuid.UUID, _: CurrentUser, db: DbSession, scope: VisibilityScope
) -> LeadDetail:
    return _detail(db, lead_service.get_lead(db, scope, lead_id))


@router.patch("/{lead_id}", response_model=LeadDetail)
def update_lead(
    lead_id: uuid.UUID,
    payload: LeadUpdate,
    request: Request,
    actor: CurrentUser,
    db: DbSession,
    scope: VisibilityScope,
) -> LeadDetail:
    lead = lead_service.get_lead(db, scope, lead_id)
    lead_service.update_lead(
        db,
        actor,
        lead,
        payload.model_dump(exclude_unset=True),
        ip_address=client_ip(request),
    )
    db.commit()
    return _detail(db, lead_service.get_lead(db, scope, lead_id))


@router.post("/{lead_id}/status", response_model=LeadDetail)
def change_status(
    lead_id: uuid.UUID,
    payload: LeadStatusChange,
    request: Request,
    actor: CurrentUser,
    db: DbSession,
    scope: VisibilityScope,
) -> LeadDetail:
    """Anyone who can see the lead may move it along — that is the assignee
    doing their job. Reassignment is the operation that needs authority."""
    lead = lead_service.get_lead(db, scope, lead_id)
    lead_service.change_status(
        db,
        actor,
        lead,
        str(payload.status),
        remark=payload.remark,
        ip_address=client_ip(request),
    )
    db.commit()
    return _detail(db, lead_service.get_lead(db, scope, lead_id))


@router.get("/{lead_id}/message", response_model=ComposedMessageOut)
def compose_message(
    lead_id: uuid.UUID,
    actor: CurrentUser,
    db: DbSession,
    scope: VisibilityScope,
    channel: str = Query(description="WHATSAPP or EMAIL", max_length=20),
) -> ComposedMessageOut:
    """The feedback request to send, already filled in.

    Only a converted lead is eligible — there is no Google review CTA here,
    that is a completed-customer thing (see /customers/{id}/message).
    """
    lead = lead_service.get_lead(db, scope, lead_id)
    if lead.dispatched_at is None:
        raise invalid("Only a converted lead can be asked for feedback.")
    # The ask belongs to the assignee, like the log it is recorded on. Letting
    # anyone else compose issued a code - and, before ISSUED existed, recorded
    # a SENT request - that the actual send then refused, leaving the queue
    # claiming an ask that never went out.
    lead_service.require_log_owner(actor, lead)

    # Issued here, not on send: the code has to be inside the link before the
    # message is written. ISSUED, not SENT - see feedback_requests.create.
    # Idempotent per lead.
    request = feedback_requests.create(
        db, actor, scope,
        subject_type="LEAD",
        subject_id=lead_id,
        channel=channel.upper(),
    )
    db.commit()

    composed = messaging.compose(
        db, lead, actor,
        purpose=messaging.Purpose.FEEDBACK,
        channel=channel.upper(),
        reference_code=request.code,
    )
    return ComposedMessageOut(**composed.as_dict())


@router.post("/{lead_id}/message/sent", response_model=LeadDetail)
def record_message_sent(
    lead_id: uuid.UUID,
    payload: SentBody,
    actor: CurrentUser,
    db: DbSession,
    scope: VisibilityScope,
) -> LeadDetail:
    """Log that the feedback request went out, so it drops off the pending
    queue and nobody asks this lead twice."""
    lead = lead_service.get_lead(db, scope, lead_id)
    if lead.dispatched_at is None:
        raise invalid("Only a converted lead can be asked for feedback.")

    channel = payload.channel.upper()
    lead_service.require_log_owner(actor, lead)
    open_request = feedback_requests.open_request_for(db, "LEAD", lead_id)
    if open_request is None:
        # Sent without composing through the portal first: issue the code now,
        # so the send is still a record something can match against.
        open_request = feedback_requests.create(
            db, actor, scope, subject_type="LEAD", subject_id=lead_id, channel=channel,
        )
    composed = messaging.compose(
        db, lead, actor,
        purpose=messaging.Purpose.FEEDBACK,
        channel=channel,
        reference_code=open_request.code if open_request else None,
    )
    text = (payload.body or composed.body).strip()
    channel_label = "WhatsApp" if channel == messaging.Channel.WHATSAPP else "email"
    lead_service.add_activity(
        db,
        actor,
        lead,
        activity_type=LeadActivityType.FEEDBACK_REQUESTED,
        remark=f"Feedback request sent by {channel_label}.\n\n{text}"[:2000],
    )
    # THE moment it counts as asked - not when the dialog was opened.
    feedback_requests.mark_sent(db, actor, open_request, channel=channel)
    db.commit()
    return _detail(db, lead_service.get_lead(db, scope, lead_id))


# Undo was removed on request (see BUSINESS_RULES_CHANGE_AUDIT.md s23). The
# route is gone, not merely the button: "do not provide the previous undo
# behavior" is not satisfied by a hidden control and a live endpoint.
#
# The timeline keeps its `undone_at` column and still renders entries that
# were undone while the feature existed. Those undos really happened; erasing
# them would be the one thing an append-only history must never do.


@router.post("/{lead_id}/activities", response_model=LeadDetail)
def add_activity(
    lead_id: uuid.UUID,
    payload: LeadActivityCreate,
    actor: CurrentUser,
    db: DbSession,
    scope: VisibilityScope,
) -> LeadDetail:
    lead = lead_service.get_lead(db, scope, lead_id)
    lead_service.add_activity(
        db,
        actor,
        lead,
        activity_type=payload.activity_type,
        remark=payload.remark,
        next_follow_up_date=payload.next_follow_up_date,
    )
    db.commit()
    return _detail(db, lead_service.get_lead(db, scope, lead_id))


def _detail(db: Session, lead, actor=None) -> LeadDetail:
    names = lead_service.decorate(db, [lead])
    actors = lead_service.activity_actors(db, lead.activities)

    detail = LeadDetail.model_validate(_to_out(lead, names))

    timeline: list[LeadActivityOut] = []
    for activity in sorted(lead.activities, key=lambda a: a.created_at, reverse=True):
        entry = LeadActivityOut.model_validate(activity)
        entry.actor_name = actors.get(activity.actor_user_id)
        # Always false: nothing on a lead can be undone any more. The field
        # stays so the response shape does not change under clients that are
        # still reading it, and so the customer timeline - which KEEPS its
        # undo - can share the same schema.
        entry.can_undo = False
        timeline.append(entry)
    detail.activities = timeline
    return detail
