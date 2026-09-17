"""Google Form responses coming back in.

`POST /feedback/sync/webhook` is the only unauthenticated route in the portal.
Everything else here needs an admin. The webhook's own door - signature,
replay window, size, schema, idempotency - lives in
`services/feedback_sync.py`; this module is the wiring.
"""
from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Query, Request, Response
from sqlalchemy import func, select

from app.core.config import settings
from app.core.constants import (
    OPEN_FEEDBACK_REQUEST_STATUSES,
    AuditAction,
    EntityType,
    FeedbackMatchStatus,
    SyncEventStatus,
    SyncSource,
)
from app.core.deps import AdminUser, DbSession, VisibilityScope, client_ip
from app.core.errors import ApiError, not_found
from app.core.ratelimit import SlidingWindowLimiter
from app.models.customer import Customer
from app.models.feedback import Feedback, FeedbackRequest, FeedbackSyncEvent
from app.models.lead import Lead
from app.schemas.feedback import (
    SyncEventOut,
    SyncResult,
    SyncStatus,
    UnmatchedResolveBody,
)
from app.services import (
    audit,
    feedback_analysis,
    feedback_requests,
    feedback_sync,
    runtime_settings,
)
from app.services.customers import get_customer

router = APIRouter(prefix="/feedback/sync", tags=["feedback"])

#: Per process, like the login limiter. Same known limitation, same reasoning.
webhook_limiter = SlidingWindowLimiter(
    max_attempts=settings.GOOGLE_SYNC_RATE_LIMIT,
    window_seconds=settings.GOOGLE_SYNC_RATE_WINDOW_SECONDS,
)


@router.post("/webhook")
async def webhook(request: Request, db: DbSession) -> dict[str, Any]:
    """Receive one Google Form response.

    Returns 200 for a duplicate on purpose: Apps Script must stop retrying
    something already handled, and a duplicate is not an error.
    """
    body = await request.body()

    # Order matters. Size before parse, signature before anything is stored:
    # unauthenticated noise must never become a write.
    try:
        feedback_sync.check_size(body)
        feedback_sync.verify(body, request.headers.get("X-Portal-Signature"))
    except feedback_sync.SyncRejected as rejected:
        raise ApiError(
            rejected.code, rejected.message, status_code=rejected.status_code
        ) from None

    source_ip = client_ip(request) or "unknown"
    allowed, retry_after = webhook_limiter.check(source_ip)
    if not allowed:
        raise ApiError(
            "RATE_LIMITED",
            "Too many deliveries. Slow down.",
            status_code=429,
            details={"retry_after_seconds": retry_after},
        )

    try:
        payload = feedback_sync.as_json(body)
    except feedback_sync.SyncRejected as rejected:
        raise ApiError(
            rejected.code, rejected.message, status_code=rejected.status_code
        ) from None

    return _ingest_one(db, payload, body, source=SyncSource.WEBHOOK)


def _ingest_one(
    db: DbSession, payload: dict, body: bytes, *, source: str
) -> dict[str, Any]:
    """One response, from either the webhook or the pull. Shared on purpose."""
    response_id = str(payload.get("response_id") or "").strip()
    answers = payload.get("answers")

    if not response_id or len(response_id) > 128 or not isinstance(answers, dict):
        raise ApiError(
            "INVALID_PAYLOAD", "That payload could not be read.", status_code=422
        )

    # Idempotency, before any work: a retried delivery is a no-op.
    seen = feedback_sync.already_seen(db, response_id)
    if seen is not None:
        return {"status": "duplicate", "response_id": response_id}

    # Written and committed FIRST. If the processing below fails, the
    # delivery is on record as arrived rather than lost.
    event = feedback_sync.record_event(
        db, external_response_id=response_id, source=source, body=body
    )
    db.commit()

    scale_max = int(runtime_settings.get(db, "feedback.rating_scale_max") or 5)
    try:
        outcome = feedback_sync.process(
            db, event, {str(k): str(v) for k, v in answers.items()}, scale_max=scale_max
        )
    except Exception:  # noqa: BLE001 - never lose the response over a bug
        db.rollback()
        event = db.get(FeedbackSyncEvent, event.id)
        if event is not None:
            event.status = SyncEventStatus.FAILED
            event.error_code = "PROCESSING_FAILED"
            event.error_detail = "The response could not be processed."
            db.commit()
        # Logged with the id only - never the payload, which carries PII.
        import logging

        logging.getLogger(__name__).exception(
            "Feedback sync failed for response %s", response_id
        )
        raise ApiError(
            "SYNC_FAILED",
            "That response could not be processed. It has been recorded for review.",
            status_code=500,
        ) from None

    feedback = (
        db.get(Feedback, outcome.feedback_id) if outcome.feedback_id else None
    )
    feedback_sync.finish_event(db, event, outcome)
    feedback_sync.notify(db, outcome, feedback)

    # New ratings may have moved a department across the threshold.
    if outcome.status == SyncEventStatus.PROCESSED:
        feedback_analysis.evaluate(db, notify=True)

    db.commit()

    return {
        "status": "stored" if outcome.status == SyncEventStatus.PROCESSED else "failed",
        "response_id": response_id,
        "feedback_id": str(outcome.feedback_id) if outcome.feedback_id else None,
        "match_status": outcome.match_status,
        "reference": outcome.reference,
    }


@router.get("/status", response_model=SyncStatus)
def sync_status(_: AdminUser, db: DbSession) -> SyncStatus:
    """Whether sync is configured, and what it has been doing.

    Booleans and counts. The secret is never returned, not even its length.
    """
    counts = {
        row.status: row.total
        for row in db.execute(
            select(
                FeedbackSyncEvent.status,
                func.count(FeedbackSyncEvent.id).label("total"),
            ).group_by(FeedbackSyncEvent.status)
        ).all()
    }
    last = db.scalar(select(func.max(FeedbackSyncEvent.received_at)))
    needs_review = (
        db.scalar(
            select(func.count(Feedback.id)).where(
                Feedback.match_status.in_(
                    [FeedbackMatchStatus.UNMATCHED, FeedbackMatchStatus.DUPLICATE]
                )
            )
        )
        or 0
    )

    return SyncStatus(
        webhook_configured=settings.feedback_sync_enabled,
        pull_configured=bool(settings.GOOGLE_SYNC_URL.strip()),
        form_url_configured=bool(
            str(runtime_settings.get(db, "company.feedback_form_url") or "").strip()
        ),
        reference_field_configured=bool(
            str(runtime_settings.get(db, "feedback.form_reference_entry_id") or "").strip()
        ),
        last_delivery_at=last,
        processed=counts.get(str(SyncEventStatus.PROCESSED), 0),
        failed=counts.get(str(SyncEventStatus.FAILED), 0),
        duplicates=counts.get(str(SyncEventStatus.DUPLICATE), 0),
        needs_review=needs_review,
    )


@router.get("/events", response_model=list[SyncEventOut])
def sync_events(
    _: AdminUser,
    db: DbSession,
    status: str | None = Query(default=None, pattern=r"^[A-Za-z_]{1,30}$"),
    limit: int = Query(default=50, ge=1, le=200),
) -> list[SyncEventOut]:
    """The delivery ledger. Hashes and outcomes, never payloads."""
    stmt = select(FeedbackSyncEvent).order_by(FeedbackSyncEvent.received_at.desc())
    if status:
        stmt = stmt.where(FeedbackSyncEvent.status == status.upper())
    return [
        SyncEventOut.model_validate(row)
        for row in db.execute(stmt.limit(limit)).scalars()
    ]


@router.post("/run", response_model=SyncResult)
def run_sync(actor: AdminUser, db: DbSession, request: Request) -> SyncResult:
    """Pull anything the webhook missed, from the Apps Script Web App.

    Safe to press repeatedly: every response goes through the same
    idempotency check as the webhook, so an already-processed one is skipped
    rather than duplicated.
    """
    url = settings.GOOGLE_SYNC_URL.strip()
    if not url or not settings.feedback_sync_enabled:
        raise ApiError(
            "NOT_CONFIGURED",
            "Feedback sync is not configured on this server.",
            status_code=503,
        )

    import httpx

    body = b"{}"
    headers = {
        "X-Portal-Signature": feedback_sync.sign(body, settings.GOOGLE_SYNC_SECRET),
        "Content-Type": "application/json",
    }
    try:
        reply = httpx.post(
            url,
            content=body,
            headers=headers,
            timeout=float(settings.GOOGLE_SYNC_TIMEOUT_SECONDS),
            follow_redirects=True,   # Apps Script Web Apps redirect once
        )
        reply.raise_for_status()
        rows = reply.json()
    except Exception:  # noqa: BLE001 - the reason is for the log, not the user
        import logging

        logging.getLogger(__name__).exception("Feedback pull failed")
        raise ApiError(
            "SYNC_UNAVAILABLE",
            "Could not reach the response sheet. Try again in a moment.",
            status_code=502,
        ) from None

    entries = rows.get("responses") if isinstance(rows, dict) else rows
    if not isinstance(entries, list):
        raise ApiError(
            "INVALID_PAYLOAD", "The response sheet returned something unexpected.",
            status_code=502,
        )

    import json as _json

    new = skipped = failed = unmatched = 0
    for entry in entries:
        if not isinstance(entry, dict):
            failed += 1
            continue
        raw = _json.dumps(entry, sort_keys=True).encode()
        try:
            result = _ingest_one(db, entry, raw, source=SyncSource.PULL)
        except ApiError:
            failed += 1
            continue
        if result["status"] == "duplicate":
            skipped += 1
        elif result["status"] == "stored":
            new += 1
            if result.get("match_status") == FeedbackMatchStatus.UNMATCHED:
                unmatched += 1
        else:
            failed += 1

    audit.record(
        db,
        actor_id=actor.id,
        action=AuditAction.FEEDBACK_SYNCED,
        entity_type=EntityType.IMPORT,
        after={"new": new, "skipped": skipped, "failed": failed},
        ip_address=client_ip(request),
    )
    db.commit()

    return SyncResult(
        new_responses=new,
        already_synced=skipped,
        unmatched=unmatched,
        errors=failed,
    )


@router.get("/customers", response_model=list[dict])
def resolvable_customers(
    _: AdminUser,
    db: DbSession,
    limit: int = Query(default=5_000, ge=1, le=5_000),
) -> list[dict]:
    """Account names, for the "file this response under..." picker.

    Exists because browsing the customer book became Super Admin only, and an
    administrator resolving an unmatched response still has to choose an
    account. Names and ids only - no contact details, no invoice history, no
    owner. Enough to identify an account, not enough to be the customer list
    by another route.
    """
    rows = db.execute(
        select(Customer.id, Customer.name, Customer.sap_code)
        .order_by(Customer.name)
        .limit(limit)
    ).all()
    return [
        {"id": str(row.id), "name": row.name, "sap_code": row.sap_code} for row in rows
    ]


@router.get("/open-requests", response_model=list[dict])
def resolvable_requests(_: AdminUser, db: DbSession) -> list[dict]:
    """Leads that were asked and have not answered - the first place an
    unmatched response should be filed. Name, company and reference only."""
    asks = feedback_requests.open_lead_requests(db)
    leads = {
        lead.id: lead
        for lead in db.execute(
            select(Lead).where(Lead.id.in_({a.lead_id for a in asks}))
        ).scalars()
    } if asks else {}
    return [
        {
            "id": str(ask.id),
            "reference": ask.reference,
            "status": ask.status,
            "name": leads[ask.lead_id].name if ask.lead_id in leads else ask.reference,
            "company_name": leads[ask.lead_id].company_name if ask.lead_id in leads else None,
        }
        for ask in asks
    ]


@router.get("/needs-review", response_model=list[dict])
def needs_review(_: AdminUser, db: DbSession) -> list[dict]:
    """Responses that arrived but could not be placed."""
    rows = db.execute(
        select(Feedback)
        .where(
            Feedback.match_status.in_(
                [FeedbackMatchStatus.UNMATCHED, FeedbackMatchStatus.DUPLICATE]
            )
        )
        .order_by(Feedback.created_at.desc())
        .limit(100)
    ).scalars()
    return [
        {
            "id": str(row.id),
            "match_status": row.match_status,
            "customer_name": row.customer_name,
            "company_name": row.company_name,
            "overall_rating": float(row.overall_rating) if row.overall_rating else None,
            "submitted_at": row.submitted_at_source,
            "created_at": row.created_at,
        }
        for row in rows
    ]


@router.post("/resolve/{feedback_id}")
def resolve(
    feedback_id: uuid.UUID,
    payload: UnmatchedResolveBody,
    actor: AdminUser,
    db: DbSession,
    scope: VisibilityScope,
    request: Request,
) -> dict[str, str]:
    """Attach an unmatched response by hand.

    Preferably to a LEAD's open ask - that completes the ask, so the lead
    leaves the pending queue and counts as answered, exactly as if the code
    had matched. Or, for historical responses, to an archived SAP customer,
    fetched through the normal scoped accessor.
    """
    feedback = db.get(Feedback, feedback_id)
    if feedback is None:
        raise not_found("Feedback not found.")

    if payload.request_id is not None:
        ask = db.get(FeedbackRequest, payload.request_id)
        if (
            ask is None
            or ask.subject_type != "LEAD"
            or ask.status not in OPEN_FEEDBACK_REQUEST_STATUSES
        ):
            raise not_found("That request is not an open ask on a lead.")
        lead = db.get(Lead, ask.lead_id)
        before = {
            "feedback_request_id": str(feedback.feedback_request_id),
            "match_status": feedback.match_status,
        }
        feedback.feedback_request_id = ask.id
        feedback.customer_id = None
        feedback.match_status = FeedbackMatchStatus.MATCHED_CONTACT
        feedback_requests.mark_completed(db, ask)
        audit.record(
            db,
            actor_id=actor.id,
            action=AuditAction.FEEDBACK_RESOLVED,
            entity_type=EntityType.FEEDBACK,
            entity_id=feedback.id,
            before=before,
            after={"feedback_request_id": str(ask.id), "reference": ask.reference},
            ip_address=client_ip(request),
        )
        db.commit()
        return {"status": "resolved", "customer": lead.name if lead else ask.reference}

    customer = get_customer(db, actor, scope, payload.customer_id)

    before = {"customer_id": str(feedback.customer_id), "match_status": feedback.match_status}
    feedback.customer_id = customer.id
    feedback.match_status = FeedbackMatchStatus.MATCHED_CONTACT

    audit.record(
        db,
        actor_id=actor.id,
        action=AuditAction.FEEDBACK_RESOLVED,
        entity_type=EntityType.FEEDBACK,
        entity_id=feedback.id,
        before=before,
        after={"customer_id": str(customer.id), "match_status": feedback.match_status},
        ip_address=client_ip(request),
    )
    db.commit()
    return {"status": "resolved", "customer": customer.name}
