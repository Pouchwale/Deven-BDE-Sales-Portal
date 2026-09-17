"""Department analysis and the low-rating alert.

The algorithm (plan v2 s9.5, kept by v3 s8.5):

  * A rolling window of the last N days.
  * A minimum sample before an average means anything — two bad ratings is a
    bad day, not a bad department.
  * Below the threshold raises ONE open alert per department, enforced by a
    partial unique index rather than a read-then-write race.
  * Recovery auto-resolves the open alert.

Evaluation runs once per import batch, not once per row: a 300-row import must
not fire 300 evaluations.
"""
from __future__ import annotations

import uuid
from datetime import timedelta
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core import authority
from app.core.authority import ALL, _All
from app.services import metrics
from app.core.constants import (
    ADMIN_ROLES,
    AlertStatus,
    FeedbackRequestStatus,
    NotificationType,
)
from app.core.errors import forbidden, not_found
from app.db.base import utcnow
from app.models.feedback import Feedback, FeedbackAlert, FeedbackDepartmentRating
from app.models.lead import Lead
from app.models.org import Department, User
from app.services import feedback_requests, notifications, runtime_settings


def _window_start(window_days: int):
    return utcnow() - timedelta(days=window_days)


def department_summary(
    db: Session,
    *,
    departments: set[uuid.UUID] | _All = ALL,
    window_days: int | None = None,
    threshold: float | None = None,
    min_responses: int | None = None,
) -> list[dict]:
    """Average rating per department over the rolling window."""
    config = runtime_settings.feedback_config(db)
    window_days = window_days if window_days is not None else config["window_days"]
    threshold = threshold if threshold is not None else config["threshold"]
    min_responses = (
        min_responses if min_responses is not None else config["min_responses"]
    )
    since = _window_start(window_days)

    # The window is on the response's own timestamp where the form gave one,
    # falling back to when we imported it.
    submitted = func.coalesce(Feedback.submitted_at_source, Feedback.created_at)

    stmt = (
        select(
            Department.id,
            Department.name,
            func.avg(FeedbackDepartmentRating.rating),
            func.count(FeedbackDepartmentRating.id),
        )
        .join(
            FeedbackDepartmentRating,
            FeedbackDepartmentRating.department_id == Department.id,
        )
        .join(Feedback, Feedback.id == FeedbackDepartmentRating.feedback_id)
        .where(FeedbackDepartmentRating.rating.is_not(None), submitted >= since)
        .group_by(Department.id, Department.name)
    )
    if departments is not ALL:
        if not departments:
            return []
        stmt = stmt.where(Department.id.in_(departments))

    rows = db.execute(stmt).all()
    windowed = {row[0]: (row[1], row[2], row[3]) for row in rows}

    # Every visible department appears, including the ones with no responses —
    # "no data" is a finding, not a row to hide.
    all_stmt = select(Department).where(Department.is_active.is_(True))
    if departments is not ALL:
        all_stmt = all_stmt.where(Department.id.in_(departments))

    summary: list[dict] = []
    for department in db.execute(all_stmt.order_by(Department.sort_order, Department.name)).scalars():
        name, average, count = windowed.get(department.id, (department.name, None, 0))
        average_value = float(average) if average is not None else None
        below = (
            average_value is not None
            and count >= min_responses
            and average_value < threshold
        )
        summary.append(
            {
                "department_id": department.id,
                "department_name": department.name,
                "average_rating": round(average_value, 2) if average_value is not None else None,
                "response_count": count,
                "below_threshold": below,
                "enough_responses": count >= min_responses,
            }
        )
    return summary


def open_alerts(db: Session, *, departments: set[uuid.UUID] | _All = ALL) -> list[FeedbackAlert]:
    stmt = select(FeedbackAlert).where(FeedbackAlert.status == AlertStatus.OPEN)
    if departments is not ALL:
        if not departments:
            return []
        stmt = stmt.where(FeedbackAlert.department_id.in_(departments))
    return list(db.execute(stmt.order_by(FeedbackAlert.opened_at.desc())).scalars().all())


def evaluate(db: Session, *, notify: bool = True) -> dict:
    """Raise and resolve department alerts. Runs once per batch.

    Returns what changed, so an import can report it rather than the caller
    guessing.
    """
    config = runtime_settings.feedback_config(db)
    summary = department_summary(db)
    existing = {alert.department_id: alert for alert in open_alerts(db)}

    raised: list[str] = []
    resolved: list[str] = []

    for row in summary:
        alert = existing.get(row["department_id"])

        if row["below_threshold"]:
            if alert is None:
                alert = FeedbackAlert(
                    department_id=row["department_id"],
                    status=AlertStatus.OPEN,
                    average_rating=Decimal(str(row["average_rating"])),
                    response_count=row["response_count"],
                    threshold=Decimal(str(config["threshold"])),
                    window_days=config["window_days"],
                )
                db.add(alert)
                db.flush()
                raised.append(row["department_name"])
                if notify:
                    _notify(db, row, config)
            else:
                # Already open: keep the numbers current without re-alerting.
                alert.average_rating = Decimal(str(row["average_rating"]))
                alert.response_count = row["response_count"]
        elif alert is not None:
            alert.status = AlertStatus.RESOLVED
            alert.resolved_at = utcnow()
            alert.resolved_reason = (
                f"Recovered to {row['average_rating']} over {row['response_count']} ratings."
                if row["average_rating"] is not None
                else "No longer enough recent ratings to flag."
            )
            resolved.append(row["department_name"])

    db.flush()
    return {"raised": raised, "resolved": resolved, "departments": len(summary)}


def _notify(db: Session, row: dict, config: dict) -> None:
    """The department's head, every Admin, and the Super Admin (plan v3 s8.5)."""
    recipients: set[uuid.UUID] = set()

    head = db.execute(
        select(User).where(User.heads_department_id == row["department_id"])
    ).scalars().first()
    if head is not None and head.is_active:
        recipients.add(head.id)

    for admin in db.execute(
        select(User).where(User.role.in_(ADMIN_ROLES), User.is_active.is_(True))
    ).scalars():
        recipients.add(admin.id)

    title = f"{row['department_name']} rating is below threshold"
    body = (
        f"{row['average_rating']}/{config['scale_max']} over "
        f"{row['response_count']} ratings in the last {config['window_days']} days. "
        f"Threshold is {config['threshold']}."
    )
    for user_id in recipients:
        notifications.create(
            db,
            user_id=user_id,
            type=NotificationType.DEPARTMENT_ALERT,
            title=title,
            body=body,
            entity_type="DEPARTMENT",
            entity_id=row["department_id"],
            # One alert notification per department per open episode.
            dedupe_key=f"alert:{user_id}:{row['department_id']}:{utcnow():%Y-%m-%d}",
        )


def pending_requests(
    db: Session, actor: User, scope: set[uuid.UUID] | _All
) -> list[dict]:
    """Everyone still owed a feedback ask.

    ONE population, and it is the same one Reference Tracking works from:
    converted portal leads whose post-sale record says they were invoiced at
    least ten days ago. This queue used to be built from SAP customers while
    references were built from leads, which is how the dashboard could report
    20 pending while the module listed 11 - both counting honestly, in two
    universes with no row in common.

    Each row carries its own state, which is what changed when requests
    became records. Previously an asked lead vanished from the queue and a
    BDE had no way to tell "nobody has asked" from "asked, still waiting":

      NOT_ASKED  no request exists
      AWAITING   a request is out and unanswered

    Answered records drop off entirely - that is what makes this "pending",
    and what keeps the dashboard KPI honest.
    """
    items: list[dict] = []
    owner_ids: set[uuid.UUID] = set()

    eligible_ids = metrics.post_sale_population(db, scope)["eligible_lead_ids"]
    leads = (
        list(db.execute(select(Lead).where(Lead.id.in_(eligible_ids))).scalars().unique())
        if eligible_ids
        else []
    )

    requests = feedback_requests.for_subjects(db, {lead.id for lead in leads}, set())
    # When the clock started for this account. `dispatched_at` when somebody
    # marked the work delivered, otherwise the day the deal was won - both are
    # real timestamps on the lead, so "waiting since" is never invented.

    # A lead whose request already completed has been answered; it belongs in
    # the responses list, not in a queue of things still owed.
    for lead in leads:
        request = requests.get(("LEAD", lead.id))
        if request is not None and request.status == FeedbackRequestStatus.COMPLETED:
            continue
        items.append(
            {
                "type": "LEAD",
                "id": lead.id,
                "name": lead.name,
                "company_name": lead.company_name,
                "mobile": lead.mobile,
                "email": lead.email,
                "since": lead.dispatched_at or lead.closed_at,
                "owner_user_id": lead.assigned_to_user_id,
                **_request_fields(request),
            }
        )
        if lead.assigned_to_user_id:
            owner_ids.add(lead.assigned_to_user_id)

    names = (
        dict(db.execute(select(User.id, User.name).where(User.id.in_(owner_ids))).all())
        if owner_ids
        else {}
    )
    for item in items:
        item["owner_name"] = names.get(item["owner_user_id"])

    # Waiting-on-somebody first, then oldest: an unanswered ask is more
    # actionable than a record nobody has touched.
    items.sort(
        key=lambda item: (item["state"] != "AWAITING", item["since"] or utcnow())
    )
    return items


def pending_count(db: Session, actor: User, scope: set[uuid.UUID] | _All) -> int:
    """`len(pending_requests(...))`, without building the rows.

    The dashboard shows only the number, and building the list loaded every
    eligible lead and every request object just to count them. Same
    population, same rule: a lead drops out when its MOST RECENT request (by
    `sent_at`, as `feedback_requests.for_subjects` picks it) is COMPLETED.
    """
    from app.models.feedback import FeedbackRequest

    eligible_ids = metrics.post_sale_population(db, scope)["eligible_lead_ids"]
    if not eligible_ids:
        return 0
    latest: dict[uuid.UUID, str] = {}
    for lead_id, status in db.execute(
        select(FeedbackRequest.lead_id, FeedbackRequest.status)
        .where(FeedbackRequest.lead_id.in_(eligible_ids))
        .order_by(FeedbackRequest.sent_at)
    ).all():
        latest[lead_id] = status  # ascending, so the last write is the newest
    return sum(
        1
        for lead_id in set(eligible_ids)
        if latest.get(lead_id) != FeedbackRequestStatus.COMPLETED
    )


def _request_fields(request) -> dict:
    """The request half of a pending row, or the not-asked shape.

    Only SENT is "awaiting". An ISSUED request was composed but never
    confirmed sent, so as far as the customer is concerned nobody has asked.
    """
    if request is None or request.status != FeedbackRequestStatus.SENT:
        return {
            "state": "NOT_ASKED",
            "request_id": None,
            "request_reference": None,
            "requested_at": None,
        }
    return {
        "state": "AWAITING",
        "request_id": request.id,
        "request_reference": request.reference,
        "requested_at": request.sent_at,
    }


def assign_alert(
    db: Session, actor: User, alert: FeedbackAlert, user_id: uuid.UUID | None
) -> FeedbackAlert:
    """Hand a flagged department to a person, or clear the assignee.

    Audited in the same transaction: who handed the alert to whom is exactly
    the question somebody asks when an alert sat unhandled.
    """
    previous = alert.assigned_to_user_id
    if user_id is None:
        alert.assigned_to_user_id = None
        db.flush()
        _audit_alert_assignment(db, actor, alert, previous)
        return alert

    assignee = db.get(User, user_id)
    if assignee is None:
        raise not_found("User not found.")
    if not authority.can_act_on(db, actor, assignee) and assignee.id != actor.id:
        raise forbidden(
            "You can only assign this to yourself or someone you can act on."
        )

    alert.assigned_to_user_id = assignee.id
    db.flush()
    _audit_alert_assignment(db, actor, alert, previous)
    return alert


def _audit_alert_assignment(
    db: Session, actor: User, alert: FeedbackAlert, previous: uuid.UUID | None
) -> None:
    from app.core.constants import AuditAction, EntityType
    from app.services import audit

    if previous == alert.assigned_to_user_id:
        return
    audit.record(
        db,
        actor_id=actor.id,
        action=AuditAction.FEEDBACK_ALERT_ASSIGNED,
        entity_type=EntityType.FEEDBACK,
        entity_id=alert.id,
        before={"assigned_to_user_id": previous},
        after={
            "assigned_to_user_id": alert.assigned_to_user_id,
            "department_id": alert.department_id,
        },
    )


def responses_per_month(db: Session, *, months: int = 6) -> list[dict]:
    """Feedback volume by month, oldest first — the trend a head looks at."""
    submitted = func.coalesce(Feedback.submitted_at_source, Feedback.created_at)
    rows = db.execute(
        select(func.strftime("%Y-%m", submitted), func.count(Feedback.id))
        .group_by(func.strftime("%Y-%m", submitted))
        .order_by(func.strftime("%Y-%m", submitted))
        if db.bind is not None and db.bind.dialect.name == "sqlite"
        else select(func.to_char(submitted, "YYYY-MM"), func.count(Feedback.id))
        .group_by(func.to_char(submitted, "YYYY-MM"))
        .order_by(func.to_char(submitted, "YYYY-MM"))
    ).all()
    return [{"month": row[0], "responses": row[1]} for row in rows if row[0]][-months:]
