"""Cross-module tools: the overview, the team, and the user's own alerts."""
from __future__ import annotations

from pydantic import BaseModel, Field

from app.core.constants import ROLE_RANK, Role
from app.services import dashboard, notifications as notification_service, users as user_service
from app.services.chat import projections as p
from app.services.chat.registry import ToolContext, ToolSpec, register


class WorkSummaryParams(BaseModel):
    pass


class TeamWorkloadParams(BaseModel):
    limit: int | None = Field(default=None, ge=1, le=50)


class NotificationsParams(BaseModel):
    unread_only: bool | None = Field(default=None)
    limit: int | None = Field(default=None, ge=1, le=50)


def _summary(ctx: ToolContext, _: WorkSummaryParams) -> dict:
    """The dashboard's own numbers, so the assistant and the dashboard can
    never disagree. `build` resolves scope internally from the actor."""
    data = dashboard.build(ctx.db, ctx.actor)
    payload = {
        "shape": data["shape"],
        "leads": data["leads"],
        "references": data["references"],
        "feedback_pending": data["feedback_pending"],
        "unread_notifications": data["org"]["unread_notifications"],
    }
    # Only present when the caller has feedback rights at all; omitted rather
    # than zeroed, so the model does not report a zero it is not entitled to.
    if data.get("feedback"):
        payload["feedback"] = data["feedback"]
    return payload


def _team(ctx: ToolContext, params: TeamWorkloadParams) -> dict:
    rows = user_service.team_workload(ctx.db, ctx.actor, ctx.scope)
    limit = ctx.clamp(params.limit)
    items = [p.team_member_row(row) for row in rows[:limit]]
    return p.listing(items, len(rows), truncated=len(rows) > limit)


def _notifications(ctx: ToolContext, params: NotificationsParams) -> dict:
    rows, total = notification_service.list_for(
        ctx.db,
        ctx.actor,
        unread_only=bool(params.unread_only),
        page=1,
        page_size=ctx.clamp(params.limit),
    )
    items = [p.notification_row(n) for n in rows]
    return p.listing(items, total, truncated=total > len(items))


register(
    ToolSpec(
        name="get_my_work_summary",
        description=(
            "A one-shot overview of everything waiting on this user: lead "
            "counts by stage, reference counters, feedback requests owed, and "
            "unread notifications. Start here for broad questions like "
            "'what should I be doing' or 'give me today's summary'."
        ),
        params_model=WorkSummaryParams,
        handler=_summary,
    )
)

register(
    ToolSpec(
        name="get_team_workload",
        description=(
            "Per-person workload for everyone in the user's reporting chain, "
            "busiest first: open leads, follow-ups due, references taken. Use "
            "for 'which of my people has the most open leads' questions."
        ),
        params_model=TeamWorkloadParams,
        handler=_team,
        # A field user has no subtree; the question is meaningless and the
        # answer would be an empty list that reads like a bug.
        min_rank=ROLE_RANK[Role.MANAGER],
    )
)

register(
    ToolSpec(
        name="get_notifications",
        description="This user's own notifications, newest first.",
        params_model=NotificationsParams,
        handler=_notifications,
    )
)
