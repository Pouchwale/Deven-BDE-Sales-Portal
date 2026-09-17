"""Customer timelines, feedback requests, and undo.

A customer from SAP is a completed lead — a won deal. Its open business is
feedback and references, so it gets a timeline of those rather than a stage
machine it has already finished.
"""
from __future__ import annotations

import uuid
from datetime import datetime

from fastapi import APIRouter, Query, Request
from pydantic import BaseModel, Field

from app.core.deps import AdminUser, CurrentUser, DbSession, VisibilityScope, client_ip
from app.schemas.common import Message
from app.schemas.messaging import ComposedMessageOut, SentBody
from app.services import customer_timeline, feedback_requests, messaging
from app.services import customers as customer_service

router = APIRouter(prefix="/customers", tags=["customer-timeline"])


class TimelineEntry(BaseModel):
    id: uuid.UUID
    kind: str
    activity_type: str
    title: str
    remark: str | None = None
    actor_name: str | None = None
    created_at: datetime
    undone_at: datetime | None = None
    can_undo: bool
    related_type: str | None = None
    related_id: uuid.UUID | None = None
    rating: float | None = None


class LogActivityBody(BaseModel):
    activity_type: str = Field(min_length=1, max_length=40)
    remark: str | None = Field(default=None, max_length=2000)


#: A customer's timeline is short in practice; this bounds the response.
TIMELINE_MAX_ENTRIES = 500


def _feedback_code(
    db, actor, scope, *, purpose: str, subject_type: str, subject_id, channel: str | None
) -> str | None:
    """Open (or reuse) the feedback request, and hand back its code.

    Created at COMPOSE time, not on send: the code has to be inside the link
    before the message is written. `create` is idempotent per subject, so
    previewing twice reuses one request rather than issuing a second code.
    """
    if purpose != messaging.Purpose.FEEDBACK:
        return None
    request = feedback_requests.create(
        db,
        actor,
        scope,
        subject_type=subject_type,
        subject_id=subject_id,
        channel=channel,
    )
    db.commit()
    return request.code


@router.get("/{customer_id}/message", response_model=ComposedMessageOut)
def compose_message(
    customer_id: uuid.UUID,
    actor: CurrentUser,
    db: DbSession,
    scope: VisibilityScope,
    purpose: str = Query(description="FEEDBACK", max_length=20),
    channel: str = Query(description="WHATSAPP or EMAIL", max_length=20),
) -> ComposedMessageOut:
    """The message to send, already filled in.

    Rendered on the server so the text that gets logged on the timeline is
    the text the template produced, not something the browser assembled.
    """
    customer = customer_service.get_customer(db, actor, scope, customer_id)
    code = _feedback_code(
        db, actor, scope,
        purpose=purpose.upper(),
        subject_type="CUSTOMER",
        subject_id=customer_id,
        channel=channel.upper(),
    )
    composed = messaging.compose(
        db, customer, actor,
        purpose=purpose.upper(),
        channel=channel.upper(),
        reference_code=code,
    )
    return ComposedMessageOut(**composed.as_dict())


@router.post("/{customer_id}/message/sent", response_model=list[TimelineEntry])
def record_sent(
    customer_id: uuid.UUID,
    payload: SentBody,
    request: Request,
    actor: CurrentUser,
    db: DbSession,
    scope: VisibilityScope,
) -> list[TimelineEntry]:
    """Log that the request went out, so nobody asks the same customer twice.

    The portal composes and opens the message; the person sends it from their
    own WhatsApp or mail client. This records that it happened.
    """
    customer = customer_service.get_customer(db, actor, scope, customer_id)
    purpose = payload.purpose.upper()
    channel = payload.channel.upper()

    open_request = feedback_requests.open_request_for(db, "CUSTOMER", customer_id)
    if open_request is None and purpose == "FEEDBACK":
        open_request = feedback_requests.create(
            db, actor, scope, subject_type="CUSTOMER", subject_id=customer_id, channel=channel,
        )
    composed = messaging.compose(
        db, customer, actor,
        purpose=purpose,
        channel=channel,
        reference_code=open_request.code if open_request else None,
    )
    text = (payload.body or composed.body).strip()
    channel_label = "WhatsApp" if channel == messaging.Channel.WHATSAPP else "email"

    customer_timeline.log(
        db,
        actor,
        scope,
        customer_id,
        activity_type="FEEDBACK_REQUESTED",
        remark=f"Feedback request sent by {channel_label}.\n\n{text}"[:2000],
    )
    if open_request is not None:
        feedback_requests.mark_sent(db, actor, open_request, channel=channel)

    db.commit()
    return get_timeline(customer_id, actor, db, scope)


@router.get("/{customer_id}/timeline", response_model=list[TimelineEntry])
def get_timeline(
    customer_id: uuid.UUID,
    actor: CurrentUser,
    db: DbSession,
    scope: VisibilityScope,
) -> list[TimelineEntry]:
    # Newest first, so the cap drops only the oldest entries.
    entries = customer_timeline.timeline(db, actor, scope, customer_id)
    return [TimelineEntry(**entry) for entry in entries[:TIMELINE_MAX_ENTRIES]]


@router.post("/{customer_id}/timeline", response_model=list[TimelineEntry])
def log_activity(
    customer_id: uuid.UUID,
    payload: LogActivityBody,
    actor: CurrentUser,
    db: DbSession,
    scope: VisibilityScope,
) -> list[TimelineEntry]:
    customer_timeline.log(
        db,
        actor,
        scope,
        customer_id,
        activity_type=payload.activity_type,
        remark=payload.remark,
    )
    db.commit()
    return get_timeline(customer_id, actor, db, scope)


@router.post("/{customer_id}/timeline/{activity_id}/undo", response_model=list[TimelineEntry])
def undo_activity(
    customer_id: uuid.UUID,
    activity_id: uuid.UUID,
    request: Request,
    actor: CurrentUser,
    db: DbSession,
    scope: VisibilityScope,
) -> list[TimelineEntry]:
    customer_timeline.undo(
        db, actor, scope, activity_id, ip_address=client_ip(request)
    )
    db.commit()
    return get_timeline(customer_id, actor, db, scope)


@router.post("/backfill-feedback-links", response_model=Message)
def backfill_links(_: AdminUser, db: DbSession) -> Message:
    """Link any feedback imported before customer matching existed.

    Administrators only: it rewrites the customer link on every unlinked
    response in the company, which is maintenance, not a field user's action.
    """
    linked = customer_timeline.backfill_feedback_links(db)
    db.commit()
    return Message(message=f"Linked {linked} response(s) to a customer.")
