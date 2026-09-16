"""Feedback tools, and the two different scopes that meet here.

The pending-request queue is scoped by PEOPLE - it is the caller's own
converted work waiting to be asked, so everyone gets their own.

The numbers are not scoped at all any more. Customer feedback is how the
company sees its own performance, so every signed-in user can read the
department averages and the reviews behind them - the same rule the Feedback
module itself now follows. A BDE who cannot see that Dispatch is at 2.9 has no
way to make sense of the complaint they are about to take.

What stays scoped by rated DEPARTMENT is the ALERT list: an alert is a job
somebody owns, not a number to read. A caller with no department scope gets an
empty list rather than a refusal, which is the honest answer - there are no
alerts that are theirs.

Contact details never appear in any of it. Not because the caller could not
read them elsewhere, but because a chat transcript is the wrong place for a
customer's phone number.
"""
from __future__ import annotations

from pydantic import BaseModel, Field
from sqlalchemy import func, select

from sqlalchemy.orm import selectinload

from app.core.authority import ALL
from app.models.feedback import Feedback, FeedbackDepartmentRating
from app.models.org import Department
from app.services import feedback_analysis, runtime_settings
from app.services.chat import projections as p
from app.services.chat.registry import ToolContext, ToolSpec, register


class PendingRequestsParams(BaseModel):
    limit: int | None = Field(default=None, ge=1, le=50)


class FeedbackAnalysisParams(BaseModel):
    months: int | None = Field(
        default=None,
        ge=1,
        le=12,
        description="Include a monthly response trend over this many months.",
    )


# Reviews are prose, and prose is expensive. Twenty-five of them is roughly
# 2,600 tokens of tool result, which on its own can exhaust a minute's
# allowance with the provider. Five answers "what did customers say" and costs
# about 250.
DEFAULT_REVIEWS = 5
MAX_REVIEWS = 10


class CustomerReviewsParams(BaseModel):
    department: str | None = Field(
        default=None,
        max_length=60,
        description="Only responses that rated this department, by name.",
    )
    search: str | None = Field(
        default=None, max_length=120, description="Match against the customer name."
    )
    limit: int | None = Field(
        default=None, ge=1, le=MAX_REVIEWS, description=f"Default {DEFAULT_REVIEWS}."
    )


def _pending(ctx: ToolContext, params: PendingRequestsParams) -> dict:
    rows = feedback_analysis.pending_requests(ctx.db, ctx.actor, ctx.scope)
    limit = ctx.clamp(params.limit)
    items = [p.pending_request_row(row) for row in rows[:limit]]
    return p.listing(items, len(rows), truncated=len(rows) > limit)


def _analysis(ctx: ToolContext, params: FeedbackAnalysisParams) -> dict:
    """Department averages and open alerts, within the caller's department scope.

    Note the scope argument on both calls. `responses_per_month` below has no
    scope of its own, which is exactly why it is only reachable from inside
    this gated tool and is not registered as one.
    """
    config = runtime_settings.feedback_config(ctx.db)
    # Company-wide, like the module. The alerts below are the scoped half.
    summary = feedback_analysis.department_summary(ctx.db, departments=ALL)
    alerts = (
        feedback_analysis.open_alerts(ctx.db, departments=ctx.department_scope)
        if ctx.department_scope is ALL or ctx.department_scope
        else []
    )
    names = {
        row.id: row.name
        for row in ctx.db.execute(select(Department.id, Department.name)).all()
    }

    payload: dict = {
        "scale_max": int(config["scale_max"]),
        "threshold": float(config["threshold"]),
        "window_days": int(config["window_days"]),
        "departments": [p.department_rating_row(row) for row in summary],
        "open_alerts": [
            p.alert_row(alert, names.get(alert.department_id)) for alert in alerts
        ],
        "count": len(summary),
    }

    if params.months:
        payload["monthly_responses"] = feedback_analysis.responses_per_month(
            ctx.db, months=params.months
        )
    return payload


register(
    ToolSpec(
        name="list_pending_feedback_requests",
        description=(
            "Converted work this user still owes a feedback request - converted "
            "leads and customers with no response on file. Scoped to the user's "
            "own accounts and their team's."
        ),
        params_model=PendingRequestsParams,
        handler=_pending,
    )
)

def _reviews(ctx: ToolContext, params: CustomerReviewsParams) -> dict:
    """The responses themselves - what each customer wrote, department by
    department. This is what the averages are made of, and until it existed
    the assistant could report a 2.4 and had nothing to say about why."""
    names = {
        row.id: row.name
        for row in ctx.db.execute(select(Department.id, Department.name)).all()
    }

    stmt = select(Feedback).options(selectinload(Feedback.department_ratings))
    if params.department:
        wanted = [
            did
            for did, name in names.items()
            if name.lower() == params.department.strip().lower()
        ]
        if not wanted:
            return p.listing([], 0)
        stmt = stmt.where(
            Feedback.id.in_(
                select(FeedbackDepartmentRating.feedback_id).where(
                    FeedbackDepartmentRating.department_id.in_(wanted)
                )
            )
        )
    if params.search:
        term = params.search.strip().lower()
        stmt = stmt.where(
            func.lower(func.coalesce(Feedback.customer_name, "")).contains(term)
            | func.lower(func.coalesce(Feedback.company_name, "")).contains(term)
        )

    total = ctx.db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    limit = min(ctx.clamp(params.limit or DEFAULT_REVIEWS), MAX_REVIEWS)
    rows = (
        ctx.db.execute(
            stmt.order_by(
                func.coalesce(Feedback.submitted_at_source, Feedback.created_at).desc()
            ).limit(limit)
        )
        .scalars()
        .unique()
        .all()
    )
    items = [p.customer_review_row(row, names) for row in rows]
    return p.listing(items, total, truncated=total > len(items))


register(
    ToolSpec(
        name="list_customer_reviews",
        description=(
            "What customers actually said. Each response carries the overall "
            "rating and comment, plus the rating and comment they left for each "
            "department. Use this when asked why a department scores the way it "
            "does, or what a particular customer thought."
        ),
        params_model=CustomerReviewsParams,
        handler=_reviews,
    )
)

register(
    ToolSpec(
        name="get_feedback_analysis",
        description=(
            "Average customer rating per department over the rolling window, "
            "which departments are below the threshold, and any open alerts. "
            "Optionally includes a monthly response trend."
        ),
        params_model=FeedbackAnalysisParams,
        handler=_analysis,
    )
)
