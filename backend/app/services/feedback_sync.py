"""Receiving a Google Form response, safely.

This is the only unauthenticated entry point in the portal, so the door is
built before anything is allowed through it. In order:

    signature -> replay window -> size -> schema -> idempotency -> ingest

It cannot use a bearer token: Apps Script has no portal session, and putting
a long-lived portal JWT into a script the company edits would be worse than a
shared secret that does exactly one thing.

The governing rule is that **a response which reached the portal is never
lost**. The sync event is written and committed before processing, so a crash
half way leaves a visible record and a retry path rather than a customer's
feedback quietly disappearing.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import time
import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.constants import (
    FeedbackMatchStatus,
    FeedbackRequestStatus,
    FeedbackSource,
    NotificationType,
    SyncEventStatus,
)
from app.db.base import utcnow
from app.models.feedback import Feedback, FeedbackRequest, FeedbackSyncEvent
from app.services import feedback_ingest, feedback_requests, notifications
from app.services.feedback_mapping import ColumnResolution, resolve_columns

logger = logging.getLogger(__name__)

#: The form question whose answer carries `FB-2026-00127.token`. Resolved
#: like every other column - by content, not position.
# One list, owned by feedback_requests, so the webhook and the importer
# cannot recognise the code column differently.
REFERENCE_PHRASES = feedback_requests.REFERENCE_PHRASES


class SyncRejected(Exception):
    """The delivery never got far enough to be recorded."""

    def __init__(self, code: str, message: str, status_code: int = 401) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code


# --------------------------------------------------------------- signature
def sign(body: bytes, secret: str, timestamp: int | None = None) -> str:
    """Build the header Apps Script sends. Used by tests and by the docs."""
    stamp = int(time.time()) if timestamp is None else timestamp
    digest = hmac.new(
        secret.encode(), f"{stamp}.".encode() + body, hashlib.sha256
    ).hexdigest()
    return f"t={stamp},v1={digest}"


def verify(body: bytes, header: str | None) -> None:
    """Raise unless this delivery is signed, recent and intact.

    Nothing here says WHICH check failed. A caller that can distinguish "bad
    signature" from "stale timestamp" can use the endpoint as an oracle.
    """
    if not settings.feedback_sync_enabled:
        raise SyncRejected("NOT_CONFIGURED", "Feedback sync is not enabled.", 503)

    if not header:
        raise SyncRejected("UNAUTHORIZED", "Not authenticated.")

    parts = dict(
        piece.split("=", 1) for piece in header.split(",") if "=" in piece
    )
    stamp, provided = parts.get("t"), parts.get("v1")
    if not stamp or not provided:
        raise SyncRejected("UNAUTHORIZED", "Not authenticated.")

    try:
        sent_at = int(stamp)
    except ValueError:
        raise SyncRejected("UNAUTHORIZED", "Not authenticated.") from None

    if abs(int(time.time()) - sent_at) > settings.GOOGLE_SYNC_MAX_SKEW_SECONDS:
        # A captured request stops working after the window, so a replay has
        # a few minutes rather than forever.
        raise SyncRejected("UNAUTHORIZED", "Not authenticated.")

    expected = hmac.new(
        settings.GOOGLE_SYNC_SECRET.encode(),
        f"{sent_at}.".encode() + body,
        hashlib.sha256,
    ).hexdigest()

    # Constant-time: a byte-by-byte compare leaks the digest one byte at a
    # time to anybody willing to measure.
    if not hmac.compare_digest(expected, provided):
        raise SyncRejected("UNAUTHORIZED", "Not authenticated.")


def check_size(body: bytes) -> None:
    if len(body) > settings.GOOGLE_SYNC_MAX_BODY_BYTES:
        raise SyncRejected("PAYLOAD_TOO_LARGE", "That payload is too large.", 413)


def payload_hash(body: bytes) -> str:
    """What gets stored instead of the payload."""
    return hashlib.sha256(body).hexdigest()


# ----------------------------------------------------------------- ingest
@dataclass
class SyncOutcome:
    status: str
    feedback_id: uuid.UUID | None = None
    match_status: str | None = None
    reference: str | None = None
    error_code: str | None = None
    error_detail: str | None = None
    unknown_departments: list[str] | None = None


def _reference_header(headers: list[str]) -> str | None:
    """Find the column carrying the request code. Shared with the importer."""
    return feedback_requests.reference_header(headers)


def already_seen(db: Session, external_response_id: str) -> FeedbackSyncEvent | None:
    return (
        db.execute(
            select(FeedbackSyncEvent).where(
                FeedbackSyncEvent.external_response_id == external_response_id
            )
        )
        .scalars()
        .first()
    )


def record_event(
    db: Session, *, external_response_id: str, source: str, body: bytes
) -> FeedbackSyncEvent:
    """The ledger row, written before anything is processed."""
    event = FeedbackSyncEvent(
        external_response_id=external_response_id,
        source=source,
        payload_hash=payload_hash(body),
        status=SyncEventStatus.RECEIVED,
    )
    db.add(event)
    db.flush()
    return event


def process(
    db: Session,
    event: FeedbackSyncEvent,
    answers: dict[str, str],
    *,
    scale_max: int,
) -> SyncOutcome:
    """Turn one response into feedback. Never raises for a data problem.

    Matching, in order of how much each identifier actually pins down:

        1. the request code   - exact, unguessable, one row
        2. mobile / email / company - exact only, via the same function the
           file importer uses
        3. nothing -> UNMATCHED, stored anyway for a human to resolve

    There is deliberately no fuzzy step. An unmatched response costs an admin
    half a minute; a MISmatched one silently corrupts a department's rolling
    average and nobody ever finds out.
    """
    headers = list(answers.keys())
    resolution = resolve_columns(headers)

    reference_header = _reference_header(headers)
    code = (answers.get(reference_header) or "").strip() if reference_header else ""

    parsed = feedback_ingest.parse_row(answers, resolution, scale_max=scale_max)

    if not feedback_ingest.has_any_rating(parsed):
        return SyncOutcome(
            status=SyncEventStatus.FAILED,
            error_code="NO_RATING",
            error_detail="The response carried no rating.",
        )

    customer_id = None
    match_status = FeedbackMatchStatus.UNMATCHED

    # The same rule the file importer uses: code, then a lead that was asked.
    request, matched = feedback_requests.match_response(
        db, code=code, mobile=parsed["mobile"], email=parsed["email"]
    )
    if request is not None:
        match_status = matched
        customer_id = request.customer_id
        if request.status == FeedbackRequestStatus.COMPLETED:
            # A second response for an ask already answered. Kept and
            # flagged - never merged, never dropped.
            match_status = FeedbackMatchStatus.DUPLICATE
    else:
        # No usable code. Fall back to the exact-match path the importer uses.
        from app.services import customer_timeline

        customer_id = customer_timeline.match_customer(
            db,
            mobile=parsed["mobile"],
            email=parsed["email"],
            company=parsed["company_name"],
        )
        if customer_id is not None:
            match_status = FeedbackMatchStatus.MATCHED_CONTACT

    # `source_row_hash` is the SECOND line of idempotency, and it is content
    # based - it exists to catch the same row arriving once by webhook and
    # once in an uploaded export. Two genuinely different responses with
    # identical answers (same second, same wording) would collide on it, so
    # a taken hash means "we have seen this content", not "reject this
    # response": the row is stored without a hash and labelled DUPLICATE.
    # Losing a real customer's feedback to a hash collision is not a trade
    # worth making.
    digest: str | None = _row_hash(answers, resolution)
    if db.execute(
        select(Feedback.id).where(Feedback.source_row_hash == digest)
    ).first():
        digest = None
        match_status = FeedbackMatchStatus.DUPLICATE

    result = feedback_ingest.create_feedback(
        db,
        parsed,
        resolution,
        answers,
        source=FeedbackSource.GOOGLE_FORMS_WEBHOOK,
        source_row_hash=digest,
        feedback_request_id=request.id if request is not None else None,
        external_response_id=event.external_response_id,
        match_status=match_status,
        customer_id=customer_id,
        # Deliberately NOT creating departments here: a typo in a form
        # question would silently spawn one, and nobody is watching.
        create_missing_departments=False,
        link_customer=request is None,
    )

    if request is not None and match_status != FeedbackMatchStatus.DUPLICATE:
        feedback_requests.mark_completed(db, request)

    return SyncOutcome(
        status=SyncEventStatus.PROCESSED,
        feedback_id=result.feedback.id,
        match_status=match_status,
        reference=request.reference if request is not None else None,
        unknown_departments=result.unknown_departments or None,
    )


def _row_hash(answers: dict[str, str], resolution: ColumnResolution) -> str:
    """Second-line idempotency, shared with the file importer.

    Catches the same response arriving once by webhook and once in an
    uploaded export, where the response ids differ but the content does not.
    """
    from app.services.feedback_import import row_hash

    return row_hash(answers, resolution)


def finish_event(
    db: Session, event: FeedbackSyncEvent, outcome: SyncOutcome
) -> None:
    event.status = outcome.status
    event.feedback_id = outcome.feedback_id
    event.error_code = outcome.error_code
    event.error_detail = (outcome.error_detail or None) and outcome.error_detail[:500]
    event.processed_at = utcnow()
    db.flush()


# ---------------------------------------------------------- notifications
def notify(db: Session, outcome: SyncOutcome, feedback: Feedback | None) -> None:
    """Tell the person whose ask it was, and admins when it needs a human.

    Deliberately NOT a per-response low-rating alert: that would fire on
    single data points and train people to ignore the department alert that
    actually matters. `feedback_analysis.evaluate` owns that.
    """
    if feedback is None:
        return

    if outcome.match_status == FeedbackMatchStatus.UNMATCHED:
        _notify_admins(db, feedback)
        return

    if feedback.feedback_request_id is None:
        # Matched on contact details alone - there is no ask, so nobody is
        # waiting on this one in particular.
        return

    request = db.get(FeedbackRequest, feedback.feedback_request_id)
    if request is None or request.owner_user_id is None:
        return

    who = feedback.company_name or feedback.customer_name or "a customer"
    rating = (
        f"{feedback.overall_rating:.0f}" if feedback.overall_rating is not None else "—"
    )
    notifications.create(
        db,
        user_id=request.owner_user_id,
        type=NotificationType.FEEDBACK_RECEIVED,
        title=f"Feedback received from {who}",
        body=f"Overall {rating}. Reference {request.reference}.",
        entity_type="FEEDBACK",
        entity_id=feedback.id,
        dedupe_key=f"feedback:{feedback.id}",
    )


def _notify_admins(db: Session, feedback: Feedback) -> None:
    """One a day, not one per response: a broken form change should produce a
    notification, not forty."""
    from app.core.constants import ADMIN_ROLES
    from app.models.org import User

    for admin in db.execute(
        select(User).where(User.role.in_(ADMIN_ROLES), User.is_active.is_(True))
    ).scalars():
        notifications.create(
            db,
            user_id=admin.id,
            type=NotificationType.FEEDBACK_UNMATCHED,
            title="A feedback response needs review",
            body="It could not be matched to a customer. See Feedback ▸ Sync.",
            entity_type="FEEDBACK",
            entity_id=feedback.id,
            dedupe_key=f"unmatched:{admin.id}:{utcnow():%Y-%m-%d}",
        )


def as_json(body: bytes) -> dict:
    try:
        payload = json.loads(body)
    except json.JSONDecodeError:
        raise SyncRejected("INVALID_PAYLOAD", "That payload could not be read.", 422) from None
    if not isinstance(payload, dict):
        raise SyncRejected("INVALID_PAYLOAD", "That payload could not be read.", 422)
    return payload
