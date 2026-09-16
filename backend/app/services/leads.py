"""Assigned leads — Module 2.

Assignment is an authority operation, not a form field: you may only hand work
to somebody you can act on, and you may only take work away from somebody you
can act on. Both go through app/core/authority.py, so Shailesh cannot pull a
lead off Ramanesh however the request is shaped.
"""
from __future__ import annotations

import uuid
from datetime import date, timedelta

from sqlalchemy import Select, func, or_, select
from sqlalchemy.orm import Session, selectinload

from app.core import authority
from app.core.authority import ALL, _All
from app.core.constants import (
    ADMIN_ROLES,
    ALLOWED_LEAD_TRANSITIONS,
    CLOSED_LEAD_STATUSES,
    REOPEN_LEAD_STATUS,
    MANUAL_ACTIVITY_TYPES,
    OPEN_LEAD_STATUSES,
    AuditAction,
    EntityType,
    ErrorCode,
    LeadActivityType,
    LeadOrigin,
    LeadStatus,
    NotificationType,
)
from app.core.errors import forbidden, invalid, not_found
from app.db.base import utcnow
from app.models.lead import Lead, LeadActivity
from app.models.org import User
from app.services import audit, notifications

# "Nothing logged for a week" is the staleness line the dashboards use.
STALE_AFTER_DAYS = 7


def _scoped(scope: set[uuid.UUID] | _All) -> Select:
    """Delegated, not duplicated.

    "Which leads may this person see" is answered in exactly one place -
    `services.metrics.scoped_leads` - so the list, the counts, the dashboard
    and the assistant cannot drift into answering it differently.
    """
    from app.services.metrics import scoped_leads

    return scoped_leads(scope)


def _require_assignable(db: Session, actor: User, assignee_id: uuid.UUID) -> User:
    """You may only hand work to somebody you can act on."""
    assignee = db.get(User, assignee_id)
    if assignee is None:
        raise invalid("That person does not exist.")
    if not assignee.is_active:
        raise invalid(f"{assignee.name} is deactivated and cannot take new work.")
    if not authority.can_act_on(db, actor, assignee):
        raise forbidden(
            f"You cannot assign work to {assignee.name}.",
            ErrorCode.FORBIDDEN,
        )
    return assignee


def get_lead(db: Session, scope: set[uuid.UUID] | _All, lead_id: uuid.UUID) -> Lead:
    """404 rather than 403 outside the caller's scope, so ids cannot be probed."""
    lead = (
        db.execute(
            _scoped(scope)
            .where(Lead.id == lead_id)
            .options(selectinload(Lead.activities))
        )
        .scalars()
        .unique()
        .one_or_none()
    )
    if lead is None:
        raise not_found("Lead not found.")
    return lead


# ------------------------------------------------------------------ read
def list_leads(
    db: Session,
    scope: set[uuid.UUID] | _All,
    *,
    status: str | None = None,
    origin: str | None = None,
    assigned_to: uuid.UUID | None = None,
    assigned_by: uuid.UUID | None = None,
    priority: str | None = None,
    open_only: bool = False,
    search: str | None = None,
    page: int = 1,
    page_size: int = 25,
) -> tuple[list[Lead], int]:
    stmt = _scoped(scope)
    if assigned_by is not None:
        stmt = stmt.where(Lead.assigned_by_user_id == assigned_by)
    if status:
        stmt = stmt.where(Lead.status == status)
    if origin:
        stmt = stmt.where(Lead.origin == origin)
    if assigned_to is not None:
        stmt = stmt.where(Lead.assigned_to_user_id == assigned_to)
    if priority:
        stmt = stmt.where(Lead.priority == priority)
    if open_only:
        stmt = stmt.where(Lead.status.in_(OPEN_LEAD_STATUSES))
    if search:
        term = search.strip().lower()
        stmt = stmt.where(
            or_(
                func.lower(Lead.name).contains(term),
                func.lower(func.coalesce(Lead.company_name, "")).contains(term),
                func.lower(func.coalesce(Lead.mobile, "")).contains(term),
                func.lower(func.coalesce(Lead.email, "")).contains(term),
            )
        )

    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = (
        db.execute(
            stmt.order_by(Lead.updated_at.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
            .options(selectinload(Lead.activities))
        )
        .scalars()
        .unique()
        .all()
    )
    return list(rows), total


def assigned_by_counts(
    db: Session, scope: set[uuid.UUID] | _All, assigner_id: uuid.UUID
) -> list[dict]:
    """How many leads this person assigned, per assignee.

    One grouped query, not a page of rows counted in the browser: the filter
    chips need the count for everybody, including assignees whose leads are
    not on the current page.

    Still scoped. A manager sees the leads they assigned that are also within
    their visibility - which in practice is all of them, since assigning to
    somebody requires authority over them, but the scope is applied rather
    than assumed.
    """
    stmt = (
        _scoped(scope)
        .where(Lead.assigned_by_user_id == assigner_id)
        .with_only_columns(Lead.assigned_to_user_id, func.count(Lead.id))
        .group_by(Lead.assigned_to_user_id)
    )
    rows = db.execute(stmt).all()
    names = dict(
        db.execute(
            select(User.id, User.name).where(
                User.id.in_([row[0] for row in rows if row[0]])
            )
        ).all()
    )
    out = [
        {
            "user_id": str(row[0]) if row[0] else None,
            "name": names.get(row[0], "Unassigned"),
            "count": row[1],
        }
        for row in rows
    ]
    out.sort(key=lambda row: (-row["count"], row["name"]))
    return out


def decorate(db: Session, leads: list[Lead]) -> dict[uuid.UUID, str]:
    """Assignee and assigner names for a page, in one query."""
    ids = {lead.assigned_to_user_id for lead in leads if lead.assigned_to_user_id}
    ids |= {lead.assigned_by_user_id for lead in leads if lead.assigned_by_user_id}
    if not ids:
        return {}
    return dict(db.execute(select(User.id, User.name).where(User.id.in_(ids))).all())


def stats(db: Session, scope: set[uuid.UUID] | _All) -> dict:
    """The lead KPIs. Stage counts come from `services.metrics`, so this
    module and the dashboard cannot report different totals; the two fields
    below are specific to this page and computed here."""
    from app.services.metrics import lead_metrics

    base = _scoped(scope).subquery()
    today = date.today()
    stale_before = utcnow() - timedelta(days=STALE_AFTER_DAYS)

    def count(*conditions) -> int:
        stmt = select(func.count()).select_from(base)
        for condition in conditions:
            stmt = stmt.where(condition)
        return db.scalar(stmt) or 0

    # Open leads whose most recent activity is older than the staleness line,
    # or which have no activity at all.
    last_activity = (
        select(
            LeadActivity.lead_id.label("lead_id"),
            func.max(LeadActivity.created_at).label("last_at"),
        )
        .group_by(LeadActivity.lead_id)
        .subquery()
    )
    no_recent = db.scalar(
        select(func.count())
        .select_from(base)
        .outerjoin(last_activity, last_activity.c.lead_id == base.c.id)
        .where(
            base.c.status.in_(OPEN_LEAD_STATUSES),
            or_(
                last_activity.c.last_at.is_(None),
                last_activity.c.last_at < stale_before,
            ),
        )
    ) or 0

    return {
        **lead_metrics(db, scope),
        "follow_ups_due": count(
            base.c.status.in_(OPEN_LEAD_STATUSES),
            base.c.next_follow_up_date.is_not(None),
            base.c.next_follow_up_date <= today,
        ),
        "no_recent_activity": no_recent,
    }


# ----------------------------------------------------------------- write
def create_lead(
    db: Session,
    actor: User,
    *,
    name: str,
    assigned_to_user_id: uuid.UUID,
    company_name: str | None = None,
    mobile: str | None = None,
    email: str | None = None,
    city: str | None = None,
    requirement: str | None = None,
    priority: str = "MEDIUM",
    next_follow_up_date: date | None = None,
    ip_address: str | None = None,
) -> Lead:
    assignee = _require_assignable(db, actor, assigned_to_user_id)

    lead = Lead(
        name=name.strip(),
        company_name=company_name,
        mobile=mobile,
        email=email,
        city=city,
        requirement=requirement,
        origin=LeadOrigin.ASSIGNED_BY_HEAD,
        status=LeadStatus.NEW,
        priority=str(priority),
        assigned_to_user_id=assignee.id,
        assigned_by_user_id=actor.id,
        assigned_at=utcnow(),
        next_follow_up_date=next_follow_up_date,
    )
    db.add(lead)
    db.flush()

    # Appended through the relationship, not db.add(): the session is
    # configured with expire_on_commit=False, so an object re-read after the
    # commit comes back from the identity map with whatever collection it had
    # already loaded. Appending keeps memory and database in step.
    lead.activities.append(
        LeadActivity(
            actor_user_id=actor.id,
            activity_type=LeadActivityType.ASSIGNED,
            to_status=LeadStatus.NEW,
            remark=f"Assigned to {assignee.name} by {actor.name}.",
        )
    )

    # Inside the same transaction: a lead that exists without its notification
    # is a lead nobody knows about.
    notifications.create(
        db,
        user_id=assignee.id,
        type=NotificationType.LEAD_ASSIGNED,
        title=f"New lead: {lead.name}",
        body=f"{actor.name} assigned you {lead.name}"
        + (f" ({company_name})" if company_name else "")
        + ".",
        entity_type=EntityType.LEAD,
        entity_id=lead.id,
    )

    audit.record(
        db,
        actor_id=actor.id,
        action=AuditAction.LEAD_CREATED,
        entity_type=EntityType.LEAD,
        entity_id=lead.id,
        after={"name": lead.name, "assigned_to_user_id": assignee.id},
        ip_address=ip_address,
    )
    db.flush()
    return lead


def update_lead(
    db: Session,
    actor: User,
    lead: Lead,
    changes: dict,
    *,
    ip_address: str | None = None,
) -> Lead:
    """Edit a lead. Reassignment is checked against BOTH ends.

    Taking work away from somebody is an act on them, so the check runs
    against the CURRENT assignee as well as the new one.
    """
    new_assignee_id = changes.get("assigned_to_user_id")
    # `assignee` stays None unless we are genuinely reassigning, so the
    # reassignment block below is the only place it is read.
    assignee: User | None = None

    if new_assignee_id is not None and new_assignee_id != lead.assigned_to_user_id:
        previous = (
            db.get(User, lead.assigned_to_user_id) if lead.assigned_to_user_id else None
        )
        # Handing off your OWN lead is not an act on somebody else, and
        # can_act_on deliberately returns False for self — so exempt it, or a
        # manager could never pass on work they were holding.
        if (
            previous is not None
            and previous.id != actor.id
            and not authority.can_act_on(db, actor, previous)
        ):
            raise forbidden(
                f"You cannot take this lead away from {previous.name}.",
                ErrorCode.FORBIDDEN,
            )
        assignee = _require_assignable(db, actor, new_assignee_id)

    editable = (
        "name",
        "company_name",
        "mobile",
        "email",
        "city",
        "requirement",
        "priority",
        "next_follow_up_date",
    )
    before = {field: getattr(lead, field) for field in editable}

    for field in editable:
        if field in changes and changes[field] is not None:
            setattr(lead, field, changes[field])

    if assignee is not None:
        previous_id = lead.assigned_to_user_id
        lead.assigned_to_user_id = assignee.id
        lead.assigned_by_user_id = actor.id
        lead.assigned_at = utcnow()

        lead.activities.append(
            LeadActivity(
                actor_user_id=actor.id,
                activity_type=LeadActivityType.REASSIGNED,
                remark=f"Reassigned to {assignee.name} by {actor.name}.",
            )
        )
        notifications.create(
            db,
            user_id=assignee.id,
            type=NotificationType.LEAD_ASSIGNED,
            title=f"Lead reassigned to you: {lead.name}",
            body=f"{actor.name} moved {lead.name} to you.",
            entity_type=EntityType.LEAD,
            entity_id=lead.id,
        )
        if previous_id is not None and previous_id != actor.id:
            notifications.create(
                db,
                user_id=previous_id,
                type=NotificationType.LEAD_REASSIGNED,
                title=f"Lead moved off your list: {lead.name}",
                body=f"{actor.name} reassigned {lead.name} to {assignee.name}.",
                entity_type=EntityType.LEAD,
                entity_id=lead.id,
            )
        audit.record(
            db,
            actor_id=actor.id,
            action=AuditAction.LEAD_REASSIGNED,
            entity_type=EntityType.LEAD,
            entity_id=lead.id,
            before={"assigned_to_user_id": previous_id},
            after={"assigned_to_user_id": assignee.id},
            ip_address=ip_address,
        )

    db.flush()

    after = {field: getattr(lead, field) for field in editable}
    changed_before, changed_after = audit.diff(before, after)
    if changed_after:
        audit.record(
            db,
            actor_id=actor.id,
            action=AuditAction.LEAD_UPDATED,
            entity_type=EntityType.LEAD,
            entity_id=lead.id,
            before=changed_before,
            after=changed_after,
            ip_address=ip_address,
        )
    return lead


def change_status(
    db: Session,
    actor: User,
    lead: Lead,
    new_status: str,
    *,
    remark: str | None = None,
    ip_address: str | None = None,
) -> Lead:
    """Move a lead along the pipeline, if the move is legal.

    The state machine is data (constants.ALLOWED_LEAD_TRANSITIONS), so the
    rules are one table to read rather than a chain of ifs.

    A remark is REQUIRED. A stage change without one says a lead moved and
    not why, which is the half that is worth reading a year later - and it is
    the half somebody needs when they inherit the account.
    """
    current = lead.status
    if new_status == current:
        return lead

    if not (remark or "").strip():
        raise invalid(
            "Add a remark describing what happened before changing the stage.",
            ErrorCode.VALIDATION_ERROR,
        )

    allowed = ALLOWED_LEAD_TRANSITIONS.get(current, frozenset())
    if new_status not in allowed:
        raise invalid(
            f"A lead cannot go from {current} to {new_status}."
            + (
                f" Allowed from here: {', '.join(sorted(allowed))}."
                if allowed
                else " This stage is final."
            ),
            ErrorCode.INVALID_TRANSITION,
            allowed=sorted(allowed),
        )

    lead.status = str(new_status)
    lead.closed_at = utcnow() if new_status in CLOSED_LEAD_STATUSES else None
    # Reaching CONVERTED is the completion action itself — "won" and
    # "delivered" are treated as the same moment, and this is what makes the
    # lead eligible for a feedback ask (see feedback_analysis.pending_requests).
    # No separate dispatch step to click through.
    if new_status == LeadStatus.CONVERTED:
        lead.dispatched_at = utcnow()
    lead.activities.append(
        LeadActivity(
            actor_user_id=actor.id,
            activity_type=LeadActivityType.STATUS_CHANGED,
            from_status=current,
            to_status=str(new_status),
            remark=remark,
        )
    )
    db.flush()

    audit.record(
        db,
        actor_id=actor.id,
        action=AuditAction.LEAD_STATUS_CHANGED,
        entity_type=EntityType.LEAD,
        entity_id=lead.id,
        before={"status": current},
        after={"status": str(new_status)},
        ip_address=ip_address,
    )
    return lead


def require_log_owner(actor: User, lead: Lead) -> None:
    """Only the person the lead is assigned to may write on its log.

    Not the manager who assigned it, not an admin, not a Super Admin. The
    operational log is a first-hand record - "called them, they want a
    revised quote" - and somebody who was not on the call cannot write that
    truthfully. Letting a manager log on a BDE's behalf turns the timeline
    from evidence into hearsay, and the BDE is the one it gets attributed to.

    Deliberately NOT the same as visibility: a manager can still read every
    lead in their chain, and can still change its stage (see `change_status`).
    Reading and reporting are management's; the log is the assignee's.
    """
    if lead.assigned_to_user_id != actor.id:
        raise forbidden(
            "You can only log activity on leads assigned to you.",
            ErrorCode.FORBIDDEN,
        )


def reopen(
    db: Session,
    actor: User,
    lead: Lead,
    *,
    remark: str,
    ip_address: str | None = None,
) -> Lead:
    """Put a lead that was closed by mistake back at the start.

    CONVERTED, LOST and JUNK are final for everyone who works leads - that is
    what makes the pipeline mean anything. This is the escape hatch for a
    mis-click, and it is deliberately narrow:

      * administrators only, because it undoes somebody else's decision;
      * back to NEW, not to the middle, so the stages are walked again rather
        than a lead appearing at QUALIFIED with no record of qualifying;
      * recorded on the timeline like any other change. Reopening is a thing
        that happened, not an erasure of the thing before it.

    It replaces Undo, which could quietly rewind anything.
    """
    if actor.role not in ADMIN_ROLES:
        raise forbidden(
            "Only an administrator can reopen a closed lead.",
            ErrorCode.FORBIDDEN,
        )
    if lead.status not in CLOSED_LEAD_STATUSES:
        raise invalid(
            f"{lead.name} is not closed - it is still at {lead.status}.",
            ErrorCode.VALIDATION_ERROR,
        )
    if not (remark or "").strip():
        raise invalid(
            "Say why this lead is being reopened.",
            ErrorCode.VALIDATION_ERROR,
        )

    previous = lead.status
    lead.status = REOPEN_LEAD_STATUS
    lead.closed_at = None
    # Converting is what made it eligible for feedback. Reopening takes that
    # back, or a lead nobody won would sit in the feedback queue.
    lead.dispatched_at = None
    lead.activities.append(
        LeadActivity(
            actor_user_id=actor.id,
            activity_type=LeadActivityType.STATUS_CHANGED,
            from_status=previous,
            to_status=REOPEN_LEAD_STATUS,
            remark=f"Reopened by {actor.name}. {remark}".strip(),
        )
    )
    db.flush()

    audit.record(
        db,
        actor_id=actor.id,
        action=AuditAction.LEAD_STATUS_CHANGED,
        entity_type=EntityType.LEAD,
        entity_id=lead.id,
        before={"status": previous},
        after={"status": REOPEN_LEAD_STATUS, "reopened": True},
        ip_address=ip_address,
    )
    return lead


def add_activity(
    db: Session,
    actor: User,
    lead: Lead,
    *,
    activity_type: str,
    remark: str | None = None,
    next_follow_up_date: date | None = None,
) -> LeadActivity:
    require_log_owner(actor, lead)
    if activity_type not in MANUAL_ACTIVITY_TYPES:
        raise invalid(
            f"{activity_type} is not something you can log by hand.",
            ErrorCode.VALIDATION_ERROR,
            allowed=list(MANUAL_ACTIVITY_TYPES),
        )
    # An entry that says "called" and nothing else is a tick box, not a
    # record. The next person to open this lead needs the sentence.
    if not (remark or "").strip():
        raise invalid(
            "Say what happened - an entry with no remark tells nobody anything.",
            ErrorCode.VALIDATION_ERROR,
        )

    activity = LeadActivity(
        actor_user_id=actor.id,
        activity_type=str(activity_type),
        remark=remark,
    )
    lead.activities.append(activity)

    if next_follow_up_date is not None:
        lead.next_follow_up_date = next_follow_up_date
    # Touch the lead so "last updated" ordering reflects the new activity.
    lead.updated_at = utcnow()
    db.flush()
    return activity


def activity_actors(db: Session, activities: list[LeadActivity]) -> dict[uuid.UUID, str]:
    ids = {a.actor_user_id for a in activities if a.actor_user_id}
    if not ids:
        return {}
    return dict(db.execute(select(User.id, User.name).where(User.id.in_(ids))).all())


# Undo was removed (BUSINESS_RULES_CHANGE_AUDIT.md s23). What replaced it is
# `reopen` above: administrators only, back to the start of the pipeline
# rather than to an arbitrary earlier point, and recorded as its own timeline
# event instead of rubbing out the one before it.
#
# The CUSTOMER timeline keeps its undo - a different surface, where a
# mis-logged call is not a business stage anybody has reported on.
