"""Reading the converted-customer book, always through a visibility scope.

Ownership rules (plan v3 s6):
  * A customer is visible to whoever owns it, and to everyone above them.
  * An unowned customer is visible to ADMIN and SUPER_ADMIN only, and is the
    pool a manager assigns from.
"""
from __future__ import annotations

import uuid

from sqlalchemy import Select, func, or_, select
from sqlalchemy.orm import Session, selectinload

from app.core.authority import ALL, _All
from app.core import eligibility
from app.core.constants import ADMIN_ROLES, ReferenceStatus
from app.core.errors import not_found
from app.models.customer import Customer, InvoiceLine
from app.models.org import User


def scoped_query(scope: set[uuid.UUID] | _All, *, include_unowned: bool) -> Select:
    """The base SELECT with the scope already applied.

    Every customer read goes through this. A repository function that cannot
    express a scope is a bug.
    """
    stmt = select(Customer)
    if scope is ALL:
        return stmt
    if not scope:
        return stmt.where(Customer.owner_user_id.in_([]))
    condition = Customer.owner_user_id.in_(scope)
    if include_unowned:
        condition = or_(condition, Customer.owner_user_id.is_(None))
    return stmt.where(condition)


def visible_to(actor: User, scope: set[uuid.UUID] | _All) -> Select:
    """Unowned customers reach administrators only."""
    return scoped_query(scope, include_unowned=actor.role in ADMIN_ROLES)


def list_customers(
    db: Session,
    actor: User,
    scope: set[uuid.UUID] | _All,
    *,
    search: str | None = None,
    reference_status: str | None = None,
    owner_id: uuid.UUID | None = None,
    unowned_only: bool = False,
    page: int = 1,
    page_size: int = 25,
) -> tuple[list[Customer], int]:
    stmt = visible_to(actor, scope)

    if search:
        term = search.strip().lower()
        # func.lower + contains is portable; ILIKE is PostgreSQL-only.
        stmt = stmt.where(
            or_(
                func.lower(Customer.name).contains(term),
                func.lower(Customer.sap_code).contains(term),
                func.lower(func.coalesce(Customer.email, "")).contains(term),
                func.lower(func.coalesce(Customer.mobile, "")).contains(term),
            )
        )
    if reference_status:
        stmt = stmt.where(Customer.reference_status == reference_status)
    if owner_id is not None:
        stmt = stmt.where(Customer.owner_user_id == owner_id)
    if unowned_only:
        stmt = stmt.where(Customer.owner_user_id.is_(None))

    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = (
        db.execute(
            stmt.order_by(Customer.name)
            .offset((page - 1) * page_size)
            .limit(page_size)
            .options(selectinload(Customer.invoice_lines))
        )
        .scalars()
        .unique()
        .all()
    )
    return list(rows), total


def get_customer(
    db: Session, actor: User, scope: set[uuid.UUID] | _All, customer_id: uuid.UUID
) -> Customer:
    """404 rather than 403 outside the caller's scope, so ids cannot be
    probed for existence across teams."""
    stmt = visible_to(actor, scope).where(Customer.id == customer_id)
    customer = (
        db.execute(stmt.options(selectinload(Customer.invoice_lines)))
        .scalars()
        .unique()
        .one_or_none()
    )
    if customer is None:
        raise not_found("Customer not found.")
    return customer


def owner_names(db: Session, customers: list[Customer]) -> dict[uuid.UUID, str]:
    """One query for every owner on the page, rather than one per row."""
    ids = {c.owner_user_id for c in customers if c.owner_user_id}
    if not ids:
        return {}
    rows = db.execute(select(User.id, User.name).where(User.id.in_(ids))).all()
    return {row[0]: row[1] for row in rows}


def stats(
    db: Session,
    actor: User,
    scope: set[uuid.UUID] | _All,
    *,
    eligible_only: bool = False,
) -> dict[str, int]:
    """Counters for the customers page header, over the same scope.

    `eligible_only` narrows to accounts SAP invoiced at least ten days ago -
    the ones that may actually be asked for a reference or feedback. The
    reference KPI passes it so its denominator is work the team could have
    done, not work nobody is allowed to do yet.
    """
    stmt = visible_to(actor, scope)
    if eligible_only:
        stmt = stmt.where(
            Customer.first_invoice_date.is_not(None),
            Customer.first_invoice_date <= eligibility.cutoff(),
        )
    base = stmt.subquery()

    def count_where(*conditions) -> int:
        stmt = select(func.count()).select_from(base)
        for condition in conditions:
            stmt = stmt.where(condition)
        return db.scalar(stmt) or 0

    total = count_where()
    unowned = count_where(base.c.owner_user_id.is_(None))

    line_stmt = select(func.count(InvoiceLine.id)).where(
        InvoiceLine.customer_id.in_(select(base.c.id))
    )
    invoice_stmt = select(func.count(func.distinct(InvoiceLine.invoice_no))).where(
        InvoiceLine.customer_id.in_(select(base.c.id))
    )

    # A completed customer's open business is feedback, so "heard from" is
    # the counterpart to the reference counters.
    from app.models.feedback import Feedback

    with_feedback = (
        db.scalar(
            select(func.count(func.distinct(Feedback.customer_id))).where(
                Feedback.customer_id.in_(select(base.c.id))
            )
        )
        or 0
    )

    return {
        "total": total,
        "owned": total - unowned,
        "unowned": unowned,
        "invoice_lines": db.scalar(line_stmt) or 0,
        "invoices": db.scalar(invoice_stmt) or 0,
        "not_asked": count_where(base.c.reference_status == ReferenceStatus.NOT_ASKED),
        "taken": count_where(base.c.reference_status == ReferenceStatus.TAKEN),
        "pending": count_where(base.c.reference_status == ReferenceStatus.PENDING),
        "declined": count_where(base.c.reference_status == ReferenceStatus.DECLINED),
        "with_feedback": with_feedback,
        "awaiting_feedback": total - with_feedback,
    }
