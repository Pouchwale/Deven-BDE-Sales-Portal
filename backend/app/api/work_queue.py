"""The two-bucket work queue.

One endpoint, two genuinely different kinds of work (plan v3 s7):

  * ASSIGNED_BY_HEAD    — leads a manager handed you.
  * REFERENCE_FOLLOWUP  — a converted customer said "ask me later" and the
                          date has arrived.

The follow-ups are sourced from `customers`, not `leads`. They stay in the
reference module and are surfaced here as work items, so they never inflate
Total Leads or Pending Leads.
"""
from __future__ import annotations

from fastapi import APIRouter, Query

from app.core.constants import LeadOrigin, OPEN_LEAD_STATUSES
from app.core.deps import CurrentUser, DbSession, VisibilityScope
from app.schemas.lead import LeadOut, WorkQueue
from app.schemas.reference import FollowUpDue
from app.services import leads as lead_service
from app.services import references as reference_service

router = APIRouter(tags=["work-queue"])


@router.get("/work-queue", response_model=WorkQueue)
def work_queue(
    actor: CurrentUser,
    db: DbSession,
    scope: VisibilityScope,
    bucket: str | None = Query(
        default=None,
        description="ASSIGNED_BY_HEAD or REFERENCE_FOLLOWUP; omit for both",
    ),
    mine_only: bool = Query(
        default=True, description="Only your own work, rather than your whole subtree"
    ),
    limit: int = Query(default=50, ge=1, le=200),
) -> WorkQueue:
    # A field user's scope is already just themselves; for a manager this is
    # the difference between "my work" and "my team's work".
    effective_scope = {actor.id} if mine_only else scope

    queue = WorkQueue()

    if bucket in (None, LeadOrigin.ASSIGNED_BY_HEAD):
        leads, total = lead_service.list_leads(
            db,
            effective_scope,
            origin=LeadOrigin.ASSIGNED_BY_HEAD,
            open_only=True,
            page=1,
            page_size=limit,
        )
        names = lead_service.decorate(db, leads)
        queue.assigned_leads = [
            _lead_out(lead, names) for lead in _by_urgency(leads)
        ]
        queue.assigned_total = total

    if bucket in (None, LeadOrigin.REFERENCE_FOLLOWUP):
        due = reference_service.follow_ups_due(db, actor, effective_scope)
        queue.reference_follow_ups = [FollowUpDue(**row) for row in due[:limit]]
        queue.follow_up_total = len(due)

    return queue


def _by_urgency(leads):
    """Overdue follow-ups first, then everything else by age."""
    return sorted(
        leads,
        key=lambda lead: (
            lead.next_follow_up_date is None,
            lead.next_follow_up_date,
            lead.created_at,
        ),
    )


def _lead_out(lead, names: dict) -> LeadOut:
    out = LeadOut.model_validate(lead)
    out.assigned_to_name = names.get(lead.assigned_to_user_id)
    out.assigned_by_name = names.get(lead.assigned_by_user_id)
    out.activity_count = len(lead.activities)
    out.last_activity_at = (
        max(a.created_at for a in lead.activities) if lead.activities else None
    )
    return out


__all__ = ["router", "OPEN_LEAD_STATUSES"]
