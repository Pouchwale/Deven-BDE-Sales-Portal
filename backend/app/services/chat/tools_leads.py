"""Lead tools.

Every one of these passes `ctx.scope` straight into the same service function
the Assigned Leads page calls. None of them takes an owner, a user id or a
scope: "show me Navya's leads" has no argument to land in.
"""
from __future__ import annotations

from datetime import timedelta

from pydantic import BaseModel, Field

from app.core.constants import OPEN_LEAD_STATUSES, LeadStatus
from app.core.errors import ApiError
from app.db.base import utcnow
from app.services import leads as lead_service
from app.services.chat import projections as p
from app.services.chat.registry import ToolContext, ToolError, ToolSpec, register


class LeadStatsParams(BaseModel):
    pass


class ListLeadsParams(BaseModel):
    status: LeadStatus | None = Field(
        default=None, description="Pipeline stage to filter by."
    )
    open_only: bool | None = Field(
        default=None, description="Only leads not yet closed (won or lost)."
    )
    search: str | None = Field(
        default=None,
        max_length=120,
        description="Match against name, company, mobile or email.",
    )
    limit: int | None = Field(default=None, ge=1, le=50, description="Max rows.")


class GetLeadParams(BaseModel):
    lead_id: str = Field(description="The lead's id, as returned by another tool.")


class OverdueLeadsParams(BaseModel):
    limit: int | None = Field(default=None, ge=1, le=50)


def _stats(ctx: ToolContext, _: LeadStatsParams) -> dict:
    return lead_service.stats(ctx.db, ctx.scope)


def _list(ctx: ToolContext, params: ListLeadsParams) -> dict:
    rows, total = lead_service.list_leads(
        ctx.db,
        ctx.scope,
        status=str(params.status) if params.status else None,
        open_only=bool(params.open_only),
        search=params.search,
        page=1,
        page_size=ctx.clamp(params.limit),
    )
    names = lead_service.decorate(ctx.db, rows)
    items = [p.lead_row(lead, p.owner_of(names, lead.assigned_to_user_id)) for lead in rows]
    return p.listing(items, total, truncated=total > len(items))


def _get(ctx: ToolContext, params: GetLeadParams) -> dict:
    try:
        lead = lead_service.get_lead(ctx.db, ctx.scope, _uuid(params.lead_id))
    except ApiError:
        # get_lead 404s outside the caller's scope, which is deliberate: a 403
        # would confirm the record exists. Keep that property here.
        raise ToolError("NOT_FOUND", "No lead with that id is visible to you.")
    names = lead_service.decorate(ctx.db, [lead])
    return p.lead_detail(
        lead,
        p.owner_of(names, lead.assigned_to_user_id),
        activity_count=len(lead.activities),
    )


def _overdue(ctx: ToolContext, params: OverdueLeadsParams) -> dict:
    """Open leads with nothing logged for a week - the same line the dashboard
    KPI and the amber row styling use."""
    rows, _total = lead_service.list_leads(
        ctx.db, ctx.scope, open_only=True, page=1, page_size=200
    )
    cutoff = utcnow() - timedelta(days=lead_service.STALE_AFTER_DAYS)

    stale = []
    for lead in rows:
        if lead.status not in OPEN_LEAD_STATUSES:
            continue
        last = max((a.created_at for a in lead.activities), default=None)
        if last is None or last < cutoff:
            stale.append((last, lead))

    stale.sort(key=lambda pair: (pair[0] is not None, pair[0]))
    limit = ctx.clamp(params.limit)
    names = lead_service.decorate(ctx.db, [lead for _, lead in stale[:limit]])

    items = []
    for last, lead in stale[:limit]:
        row = p.lead_row(lead, names.get(lead.assigned_to_user_id))
        row["days_since_touch"] = (
            None if last is None else (utcnow() - last).days
        )
        items.append(row)

    return p.listing(items, len(stale), truncated=len(stale) > len(items))


def _uuid(value: str):
    import uuid as _u

    try:
        return _u.UUID(str(value))
    except (ValueError, AttributeError, TypeError):
        raise ToolError("INVALID_ARGUMENTS", "That is not a valid id.")


register(
    ToolSpec(
        name="get_lead_stats",
        description=(
            "Counts of the user's leads by pipeline stage, plus follow-ups due "
            "and leads with no recent activity. Use for 'how many leads do I "
            "have' style questions before listing anything."
        ),
        params_model=LeadStatsParams,
        handler=_stats,
    )
)

register(
    ToolSpec(
        name="list_leads",
        description=(
            "List the leads this user can see, newest activity first. Filter by "
            "stage, by open-only, or by a search term. Returns at most 25 rows."
        ),
        params_model=ListLeadsParams,
        handler=_list,
    )
)

register(
    ToolSpec(
        name="get_lead",
        description=(
            "Full detail for one lead, including contact details and its "
            "reference status. Requires an id from another tool."
        ),
        params_model=GetLeadParams,
        handler=_get,
    )
)

register(
    ToolSpec(
        name="list_overdue_leads",
        description=(
            "Open leads with nothing logged for seven days or more, oldest "
            "first. Use for 'what is overdue' or 'what needs attention'."
        ),
        params_model=OverdueLeadsParams,
        handler=_overdue,
    )
)
