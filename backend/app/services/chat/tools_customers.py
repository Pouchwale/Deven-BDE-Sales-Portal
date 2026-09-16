"""Customer tools. Super Admin only, matching the HTTP boundary.

The SAP customer book is a historical archive now - nothing operational is
computed from it, and browsing it became Super Admin only when that changed.
The assistant must not be a second door into what the UI closed: a BDE who
cannot open /customers must not be able to ask for the same rows in a
sentence. `min_rank` is what enforces that, and the dispatcher applies it
before the handler ever runs.

Scope still applies underneath - `customers.visible_to` decides whether
unowned accounts are included - so this narrows the audience without
loosening anything below it.
"""
from __future__ import annotations

from pydantic import BaseModel, Field

from app.core.constants import ROLE_RANK, ReferenceStatus, Role
from app.core.errors import ApiError
from app.services import customer_timeline, customers as customer_service
from app.services.chat import projections as p
from app.services.chat.registry import ToolContext, ToolError, ToolSpec, register


class ListCustomersParams(BaseModel):
    search: str | None = Field(
        default=None,
        max_length=120,
        description="Match against name, SAP code, email or mobile.",
    )
    reference_status: ReferenceStatus | None = Field(
        default=None, description="Filter by where the account sits in the reference cycle."
    )
    limit: int | None = Field(default=None, ge=1, le=50)


class GetCustomerParams(BaseModel):
    customer_id: str = Field(description="The customer's id, from another tool.")


class CustomerTimelineParams(BaseModel):
    customer_id: str = Field(description="The customer's id, from another tool.")
    limit: int | None = Field(default=None, ge=1, le=50)


def _list(ctx: ToolContext, params: ListCustomersParams) -> dict:
    rows, total = customer_service.list_customers(
        ctx.db,
        ctx.actor,
        ctx.scope,
        search=params.search,
        reference_status=str(params.reference_status) if params.reference_status else None,
        page=1,
        page_size=ctx.clamp(params.limit),
    )
    owners = customer_service.owner_names(ctx.db, rows)
    items = [p.customer_row(c, p.owner_of(owners, c.owner_user_id)) for c in rows]
    return p.listing(items, total, truncated=total > len(items))


def _get(ctx: ToolContext, params: GetCustomerParams) -> dict:
    customer = _load(ctx, params.customer_id)
    owners = customer_service.owner_names(ctx.db, [customer])
    return p.customer_detail(customer, p.owner_of(owners, customer.owner_user_id))


def _timeline(ctx: ToolContext, params: CustomerTimelineParams) -> dict:
    customer = _load(ctx, params.customer_id)
    entries = customer_timeline.timeline(ctx.db, ctx.actor, ctx.scope, customer.id)
    limit = ctx.clamp(params.limit)
    items = [p.timeline_entry_row(entry) for entry in entries[:limit]]
    return p.listing(items, len(entries), truncated=len(entries) > limit)


def _load(ctx: ToolContext, raw_id: str):
    import uuid as _u

    try:
        customer_id = _u.UUID(str(raw_id))
    except (ValueError, AttributeError, TypeError):
        raise ToolError("INVALID_ARGUMENTS", "That is not a valid id.")
    try:
        return customer_service.get_customer(ctx.db, ctx.actor, ctx.scope, customer_id)
    except ApiError:
        # 404 rather than 403, matching the service: an id outside your scope
        # must not be confirmed to exist.
        raise ToolError("NOT_FOUND", "No customer with that id is visible to you.")


register(
    ToolSpec(
        name="list_customers",
        description=(
            "The archived SAP customer book. HISTORICAL only - no portal "
            "metric is computed from it. For who has been won, who can be "
            "asked for a reference, or who owes feedback, use the lead and "
            "reference tools instead."
        ),
        params_model=ListCustomersParams,
        handler=_list,
        min_rank=ROLE_RANK[Role.SUPER_ADMIN],
    )
)

register(
    ToolSpec(
        name="get_customer",
        description=(
            "Full detail for one customer, including contact details, invoice "
            "count and reference state."
        ),
        params_model=GetCustomerParams,
        handler=_get,
        min_rank=ROLE_RANK[Role.SUPER_ADMIN],
    )
)

register(
    ToolSpec(
        name="get_customer_timeline",
        description=(
            "What has happened with one customer: notes, calls, feedback "
            "requests, reference asks and the responses themselves, newest "
            "first. Use for 'what happened with <customer>' questions."
        ),
        params_model=CustomerTimelineParams,
        handler=_timeline,
        min_rank=ROLE_RANK[Role.SUPER_ADMIN],
    )
)
