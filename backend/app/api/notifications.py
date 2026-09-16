"""In-app notifications."""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Query
from pydantic import BaseModel

from app.core.deps import CurrentUser, DbSession
from app.schemas.common import Message, ORMModel, Page
from app.services import notifications as notification_service

router = APIRouter(prefix="/notifications", tags=["notifications"])


class NotificationOut(ORMModel):
    id: uuid.UUID
    type: str
    title: str
    body: str | None = None
    entity_type: str | None = None
    entity_id: uuid.UUID | None = None
    is_read: bool
    created_at: object  # datetime, kept loose so the ORM value passes straight through


class UnreadCount(BaseModel):
    unread: int


@router.get("/unread-count", response_model=UnreadCount)
def unread_count(user: CurrentUser, db: DbSession) -> UnreadCount:
    """Polled by the bell. Also the moment we top up follow-up reminders.

    Generating them here rather than on a schedule keeps the deployment to two
    processes instead of three, and the unique dedupe key makes a burst of
    polls idempotent.
    """
    created = notification_service.ensure_followup_notifications(db, user)
    if created:
        db.commit()
    return UnreadCount(unread=notification_service.unread_count(db, user))


@router.get("", response_model=Page[NotificationOut])
def list_notifications(
    user: CurrentUser,
    db: DbSession,
    unread_only: bool = Query(default=False),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=25, ge=1, le=100),
) -> Page[NotificationOut]:
    notification_service.ensure_followup_notifications(db, user)
    db.commit()

    rows, total = notification_service.list_for(
        db, user, unread_only=unread_only, page=page, page_size=page_size
    )
    return Page[NotificationOut](
        items=[NotificationOut.model_validate(row) for row in rows],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.post("/{notification_id}/read", response_model=Message)
def mark_read(notification_id: uuid.UUID, user: CurrentUser, db: DbSession) -> Message:
    found = notification_service.mark_read(db, user, notification_id)
    db.commit()
    # Same answer either way: whether somebody else's notification exists is
    # not information this endpoint should leak.
    return Message(message="Marked as read." if found else "Nothing to do.")


@router.post("/read-all", response_model=Message)
def mark_all_read(user: CurrentUser, db: DbSession) -> Message:
    count = notification_service.mark_all_read(db, user)
    db.commit()
    return Message(message=f"Marked {count} notification(s) as read.")
