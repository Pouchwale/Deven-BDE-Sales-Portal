"""Reference tracking — Module 1."""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Query, Request, status

from app.core.constants import ReferenceOutcome, ReferenceStatus
from app.core.deps import CurrentUser, DbSession, VisibilityScope, client_ip
from app.schemas.common import MAX_LIST_ITEMS, MAX_PAGE, MAX_PAGE_SIZE, Page
from app.schemas.reference import (
    AskableAccount,
    FollowUpDue,
    ReferenceCreate,
    ReferenceOut,
    ReferenceStats,
)
from app.services import references as reference_service

router = APIRouter(prefix="/references", tags=["references"])


def _to_out(reference, lookups: dict) -> ReferenceOut:
    out = ReferenceOut.model_validate(reference)
    out.customer_name = lookups["customers"].get(reference.customer_id)
    out.customer_sap_code = lookups["codes"].get(reference.customer_id)
    out.requested_by_name = lookups["users"].get(reference.requested_by_user_id)
    # One field for "who gave us this", whichever table they live in.
    if reference.lead_id is not None:
        out.source_type = "LEAD"
        out.source_name = lookups["leads"].get(reference.lead_id)
    else:
        out.source_type = "CUSTOMER"
        out.source_name = out.customer_name
    return out


@router.get("/stats", response_model=ReferenceStats)
def reference_stats(
    actor: CurrentUser, db: DbSession, scope: VisibilityScope
) -> ReferenceStats:
    return ReferenceStats(**reference_service.stats(db, actor, scope))


@router.get("/follow-ups", response_model=list[FollowUpDue])
def follow_ups(
    actor: CurrentUser,
    db: DbSession,
    scope: VisibilityScope,
    include_future: bool = Query(default=False),
    group_by: str = Query(default="due_date", pattern="^(due_date|owner)$"),
    limit: int = Query(default=MAX_LIST_ITEMS, ge=1, le=MAX_LIST_ITEMS),
) -> list[FollowUpDue]:
    """The reference follow-up bucket of the work queue."""
    rows = reference_service.follow_ups_due(
        db, actor, scope, include_future=include_future, group_by=group_by
    )
    return [FollowUpDue(**row) for row in rows[:limit]]


#: The Reference Tracking page pages this list in the browser, so the cap is
#: generous - it exists to bound the response, not to page it.
MAX_ASKABLE_ACCOUNTS = 5_000


@router.get("/accounts", response_model=list[AskableAccount])
def askable_accounts(
    actor: CurrentUser,
    db: DbSession,
    scope: VisibilityScope,
    reference_status: ReferenceStatus | None = Query(default=None),
    search: str | None = Query(default=None, max_length=120),
    owner_id: uuid.UUID | None = Query(default=None),
    limit: int = Query(default=MAX_ASKABLE_ACCOUNTS, ge=1, le=MAX_ASKABLE_ACCOUNTS),
) -> list[AskableAccount]:
    """Every won account that can be asked for a reference.

    ONE source: leads the portal saw converted, whose post-sale record says
    they were invoiced at least ten days ago. Filtering happens HERE, in the
    database, over the caller's scope - never in the browser over a page that
    happens to have been fetched.
    """
    rows = reference_service.askable_accounts(
        db,
        actor,
        scope,
        reference_status=str(reference_status) if reference_status else None,
        search=search,
        owner_id=owner_id,
    )
    return [AskableAccount(**row) for row in rows[:limit]]


@router.get("", response_model=Page[ReferenceOut])
def list_references(
    db: DbSession,
    scope: VisibilityScope,
    _: CurrentUser,
    customer_id: uuid.UUID | None = Query(default=None),
    outcome: ReferenceOutcome | None = Query(default=None),
    search: str | None = Query(default=None, max_length=120),
    page: int = Query(default=1, ge=1, le=MAX_PAGE),
    page_size: int = Query(default=25, ge=1, le=MAX_PAGE_SIZE),
) -> Page[ReferenceOut]:
    rows, total = reference_service.list_references(
        db,
        scope,
        customer_id=customer_id,
        outcome=str(outcome) if outcome else None,
        search=search,
        page=page,
        page_size=page_size,
    )
    lookups = reference_service.decorate(db, rows)
    return Page[ReferenceOut](
        items=[_to_out(r, lookups) for r in rows],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.post("", response_model=ReferenceOut, status_code=status.HTTP_201_CREATED)
def record_reference(
    payload: ReferenceCreate,
    request: Request,
    actor: CurrentUser,
    db: DbSession,
    scope: VisibilityScope,
) -> ReferenceOut:
    reference = reference_service.record(
        db,
        actor,
        scope,
        customer_id=payload.customer_id,
        lead_id=payload.lead_id,
        outcome=str(payload.outcome),
        asked_on=payload.asked_on,
        next_reference_date=payload.next_reference_date,
        referred_name=payload.referred_name,
        referred_company=payload.referred_company,
        referred_mobile=payload.referred_mobile,
        referred_email=payload.referred_email,
        notes=payload.notes,
        requested_by_user_id=payload.requested_by_user_id,
        ip_address=client_ip(request),
    )
    db.commit()
    db.refresh(reference)
    return _to_out(reference, reference_service.decorate(db, [reference]))
