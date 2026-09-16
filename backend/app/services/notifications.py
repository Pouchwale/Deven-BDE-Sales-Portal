"""In-app notifications.

Two rules:

  * `create` never commits. A notification is added to the caller's session so
    it lands in the same transaction as the thing it announces — a lead
    assignment that half-succeeds is worse than one that fails outright.
  * Reference follow-up reminders are generated lazily, on the notification
    poll, rather than by a scheduler. A unique dedupe key of
    (user, customer, date) makes that fire once per customer per day however
    often the client polls. Simpler than a cron job and adequate at this scale.
"""
from __future__ import annotations

import uuid
from datetime import date

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.constants import NotificationType
from app.db.base import utcnow
from app.models.system import Notification
from app.models.org import User


def create(
    db: Session,
    *,
    user_id: uuid.UUID,
    type: NotificationType | str,
    title: str,
    body: str | None = None,
    entity_type: str | None = None,
    entity_id: uuid.UUID | None = None,
    dedupe_key: str | None = None,
) -> Notification | None:
    """Add a notification. Returns None when the dedupe key already exists."""
    if dedupe_key is not None:
        existing = db.execute(
            select(Notification.id).where(Notification.dedupe_key == dedupe_key)
        ).first()
        if existing is not None:
            return None

    notification = Notification(
        user_id=user_id,
        type=str(type),
        title=title,
        body=body,
        entity_type=entity_type,
        entity_id=entity_id,
        dedupe_key=dedupe_key,
    )
    db.add(notification)
    return notification


def unread_count(db: Session, user: User) -> int:
    return (
        db.scalar(
            select(func.count(Notification.id)).where(
                Notification.user_id == user.id, Notification.is_read.is_(False)
            )
        )
        or 0
    )


def list_for(
    db: Session,
    user: User,
    *,
    unread_only: bool = False,
    page: int = 1,
    page_size: int = 25,
) -> tuple[list[Notification], int]:
    stmt = select(Notification).where(Notification.user_id == user.id)
    if unread_only:
        stmt = stmt.where(Notification.is_read.is_(False))

    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = (
        db.execute(
            stmt.order_by(Notification.created_at.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        .scalars()
        .all()
    )
    return list(rows), total


def mark_read(db: Session, user: User, notification_id: uuid.UUID) -> bool:
    notification = db.get(Notification, notification_id)
    # Scoped to the caller: reading somebody else's notification by id is not
    # a thing that should work.
    if notification is None or notification.user_id != user.id:
        return False
    if not notification.is_read:
        notification.is_read = True
        notification.read_at = utcnow()
    return True


def mark_all_read(db: Session, user: User) -> int:
    rows = (
        db.execute(
            select(Notification).where(
                Notification.user_id == user.id, Notification.is_read.is_(False)
            )
        )
        .scalars()
        .all()
    )
    now = utcnow()
    for notification in rows:
        notification.is_read = True
        notification.read_at = now
    return len(rows)


# ------------------------------------------------- lazy follow-up reminders
def ensure_followup_notifications(db: Session, user: User) -> int:
    """Create any missing "reference follow-up due" reminders for this user.

    Reads the same population Reference Tracking does: converted leads the
    person is responsible for. It used to read the SAP customer book, so a
    reminder could arrive about an account that did not appear anywhere in
    the module the reminder linked to.

    The dedupe key is `followup:<user>:<lead>:<date>`, and the column is
    unique, so a burst of polls cannot produce duplicates.
    """
    from app.core.constants import LeadStatus, ReferenceStatus
    from app.models.lead import Lead

    today = date.today()
    due = (
        db.execute(
            select(Lead).where(
                Lead.assigned_to_user_id == user.id,
                Lead.status == LeadStatus.CONVERTED,
                Lead.reference_status == ReferenceStatus.PENDING,
                Lead.next_reference_date.is_not(None),
                Lead.next_reference_date <= today,
            )
        )
        .scalars()
        .all()
    )

    created = 0
    for lead in due:
        notification = create(
            db,
            user_id=user.id,
            type=NotificationType.REFERENCE_FOLLOWUP_DUE,
            title=f"Reference follow-up due: {lead.name}",
            body=(
                f"You said you would ask {lead.name} again on "
                f"{lead.next_reference_date:%d %b %Y}."
            ),
            entity_type="LEAD",
            entity_id=lead.id,
            dedupe_key=f"followup:{user.id}:{lead.id}:{today.isoformat()}",
        )
        if notification is not None:
            created += 1
    return created
