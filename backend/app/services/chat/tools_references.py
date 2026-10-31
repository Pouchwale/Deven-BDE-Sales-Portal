"""Reference-tracking tools.

Covers both kinds of won account - invoiced SAP customers and leads converted
in the portal - because the reference module already merges them at read time.
"""
from __future__ import annotations

from pydantic import BaseModel, Field

from app.core.constants import ReferenceOutcome, ReferenceStatus
from app.services import references as reference_service
from app.services.chat import projections as p
from app.services.chat.registry import ToolContext, ToolSpec, register


class ReferenceStatsParams(BaseModel):
    pass


class AskableAccountsParams(BaseModel):
    reference_status: ReferenceStatus | None = Field(
        default=None,
        description="Filter to accounts in one reference state, e.g. NOT_ASKED.",
    )
    mine: bool = Field(
        default=False,
        description=(
            "Only accounts owned by the user themself. A manager's 'my tasks' / "
            "'my own accounts' / 'my list' - as opposed to their team's."
        ),
    )
    open_only: bool = Field(
        default=False,
        description=(
            "Only accounts still owing an answer (not asked yet, or said "
            "'later'). With mine=true this is exactly the My tasks tab."
        ),
    )
    limit: int | None = Field(default=None, ge=1, le=50)


class ListReferencesParams(BaseModel):
    outcome: ReferenceOutcome | None = Field(
        default=None,
        description="YES returns referrals we were actually given; NO returns 'ask me later'.",
    )
    search: str | None = Field(
        default=None,
        max_length=120,
        description="Match against the referred person's name, company or mobile.",
    )
    limit: int | None = Field(default=None, ge=1, le=50)


class FollowUpsParams(BaseModel):
    limit: int | None = Field(default=None, ge=1, le=50)


def _stats(ctx: ToolContext, _: ReferenceStatsParams) -> dict:
    return reference_service.stats(ctx.db, ctx.actor, ctx.scope)


def _accounts(ctx: ToolContext, params: AskableAccountsParams) -> dict:
    rows = reference_service.askable_accounts(
        ctx.db,
        ctx.actor,
        ctx.scope,
        reference_status=str(params.reference_status) if params.reference_status else None,
        open_only=params.open_only,
        owner_id=ctx.actor.id if params.mine else None,
    )
    limit = ctx.clamp(params.limit)
    items = [p.askable_account_row(row) for row in rows[:limit]]
    return p.listing(items, len(rows), truncated=len(rows) > limit)


def _references(ctx: ToolContext, params: ListReferencesParams) -> dict:
    rows, total = reference_service.list_references(
        ctx.db,
        ctx.scope,
        outcome=str(params.outcome) if params.outcome else None,
        search=params.search,
        page=1,
        page_size=ctx.clamp(params.limit),
    )
    lookups = reference_service.decorate(ctx.db, rows)

    items = []
    for reference in rows:
        if reference.lead_id is not None:
            source = lookups["leads"].get(reference.lead_id)
        else:
            source = lookups["customers"].get(reference.customer_id)
        items.append(p.reference_row(reference, source))

    return p.listing(items, total, truncated=total > len(items))


def _follow_ups(ctx: ToolContext, params: FollowUpsParams) -> dict:
    rows = reference_service.follow_ups_due(
        ctx.db,
        ctx.actor,
        ctx.scope,
    )
    limit = ctx.clamp(params.limit)
    items = [p.follow_up_row(row) for row in rows[:limit]]
    return p.listing(items, len(rows), truncated=len(rows) > limit)


register(
    ToolSpec(
        name="get_reference_stats",
        description=(
            "Reference counters for the accounts this user can see: how many "
            "won accounts exist, how many gave a reference, how many are "
            "pending, the reference rate, and follow-ups due."
        ),
        params_model=ReferenceStatsParams,
        handler=_stats,
    )
)

register(
    ToolSpec(
        name="list_reference_accounts",
        description=(
            "Won accounts that can be asked for a reference - both invoiced SAP "
            "customers and leads converted in the portal. Use for 'who haven't "
            "we asked yet' by filtering reference_status=NOT_ASKED. For a "
            "manager's own 'my tasks', pass mine=true and open_only=true; "
            "without mine they get their whole team's accounts."
        ),
        params_model=AskableAccountsParams,
        handler=_accounts,
    )
)

register(
    ToolSpec(
        name="list_references",
        description=(
            "Reference asks that have been recorded. outcome=YES gives the "
            "people we were actually referred to, with their contact details."
        ),
        params_model=ListReferencesParams,
        handler=_references,
    )
)

register(
    ToolSpec(
        name="list_reference_followups",
        description=(
            "Accounts that said 'ask me later' (every one, whatever date "
            "was set), earliest ask-again date first."
        ),
        params_model=FollowUpsParams,
        handler=_follow_ups,
    )
)
