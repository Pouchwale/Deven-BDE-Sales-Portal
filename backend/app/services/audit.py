"""The audit trail.

`record` never commits. It adds the row to the caller's session so the audit
entry lands in the same transaction as the change it describes - either both
are durable or neither is. An audit log that can disagree with the data it
audits is worse than no audit log.
"""
from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy.orm import Session

from app.core.constants import AuditAction, EntityType
from app.models.system import AuditEvent

# Never written to the audit trail, whatever the caller passes.
_REDACTED_FIELDS = frozenset(
    {
        "hashed_password",
        "password_encrypted",
        "password",
        "new_password",
        "old_password",
        "token",
        "access_token",
        "session_token",
        "token_hash",
        "secret",
        "api_key",
        "csrf_token",
    }
)


def _json_safe(value: Any) -> Any:
    """Values the JSON column can store.

    A `date` used to reach the column raw - e.g. editing a lead's follow-up
    date - and the whole request failed with a 500 because the audit row could
    not be serialised.
    """
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    return value


def _clean(payload: dict[str, Any] | None) -> dict[str, Any] | None:
    if not payload:
        return None
    return {
        key: _json_safe(value)
        for key, value in payload.items()
        if key not in _REDACTED_FIELDS
    }


def diff(before: dict[str, Any], after: dict[str, Any]) -> tuple[dict, dict]:
    """Only the fields that actually changed.

    Storing whole records would make the log unreadable and would repeatedly
    persist fields nobody touched.
    """
    changed = {k for k in after if before.get(k) != after.get(k)}
    return (
        {k: before.get(k) for k in changed},
        {k: after.get(k) for k in changed},
    )


def record(
    db: Session,
    *,
    actor_id: uuid.UUID | None,
    action: AuditAction | str,
    entity_type: EntityType | str | None = None,
    entity_id: uuid.UUID | None = None,
    before: dict[str, Any] | None = None,
    after: dict[str, Any] | None = None,
    ip_address: str | None = None,
) -> AuditEvent:
    event = AuditEvent(
        actor_user_id=actor_id,
        action=str(action),
        entity_type=str(entity_type) if entity_type else None,
        entity_id=entity_id,
        before=_clean(before),
        after=_clean(after),
        ip_address=ip_address,
    )
    db.add(event)
    return event
