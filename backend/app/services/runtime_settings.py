"""Settings an admin can change without a deploy.

`app_settings` is the source of truth at runtime; the environment supplies the
fallback for a key that has not been seeded yet. Reads are typed, so a caller
gets a float rather than a string that happens to look like one.
"""
from __future__ import annotations

import json
import re
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings as env_settings
from app.db.base import utcnow
from app.models.system import AppSetting
from app.seeds import message_templates as templates

# key -> (type, environment fallback)
DEFAULTS: dict[str, tuple[str, Any]] = {
    "feedback.rating_scale_max": ("int", env_settings.FEEDBACK_RATING_SCALE_MAX),
    "feedback.alert_threshold": ("float", env_settings.FEEDBACK_ALERT_THRESHOLD),
    "feedback.alert_min_responses": ("int", env_settings.FEEDBACK_ALERT_MIN_RESPONSES),
    "feedback.alert_window_days": ("int", env_settings.FEEDBACK_ALERT_WINDOW_DAYS),
    "reference.default_followup_days": ("int", 30),
    # Messaging. Defaults live in app/seeds/roster.py so the seeded row and
    # the fallback cannot drift apart.
    "company.name": ("str", "Pouchwale"),
    "company.feedback_form_url": ("str", ""),
    "feedback.form_reference_entry_id": ("str", ""),
    "feedback.request_expires_days": ("int", 30),
    "message.feedback_whatsapp": ("str", templates.FEEDBACK_WHATSAPP),
    "message.feedback_email_subject": ("str", templates.FEEDBACK_EMAIL_SUBJECT),
    "message.feedback_email_body": ("str", templates.FEEDBACK_EMAIL_BODY),
}


def _coerce(value: str | None, value_type: str, fallback: Any) -> Any:
    if value is None or value == "":
        return fallback
    try:
        if value_type == "int":
            return int(float(value))
        if value_type == "float":
            return float(value)
        if value_type == "bool":
            return value.strip().lower() in {"1", "true", "yes", "on"}
        if value_type == "json":
            return json.loads(value)
    except (TypeError, ValueError, json.JSONDecodeError):
        # A malformed stored value falls back rather than taking the app down.
        return fallback
    return value


def get(db: Session, key: str) -> Any:
    value_type, fallback = DEFAULTS.get(key, ("str", None))
    row = db.get(AppSetting, key)
    return _coerce(row.value if row else None, row.value_type if row else value_type, fallback)


def all_settings(db: Session) -> dict[str, Any]:
    rows = {row.key: row for row in db.execute(select(AppSetting)).scalars()}
    out: dict[str, Any] = {}
    for key, (value_type, fallback) in DEFAULTS.items():
        row = rows.get(key)
        out[key] = _coerce(row.value if row else None, row.value_type if row else value_type, fallback)
    return out


#: Longest text setting (the email body template is the long one).
MAX_TEXT_SETTING_LENGTH = 10_000
#: Numeric settings are counts, days, scales and thresholds - never negative,
#: never astronomically large.
MAX_NUMERIC_SETTING = 100_000


class InvalidSetting(ValueError):
    """A value an administrator tried to store that the setting cannot hold."""


def validate_value(key: str, value: Any) -> Any:
    """Check a new value against its setting's type. Returns it unchanged.

    Raises InvalidSetting with a sentence naming the key. Keys ending in
    `_url` must be empty or an http(s) URL: the value is put into messages
    customers open, so `javascript:` or `file:` links are refused.
    """
    value_type = DEFAULTS.get(key, ("str", None))[0]
    if value_type in ("int", "float"):
        if isinstance(value, bool) or value is None:
            raise InvalidSetting(f"{key} must be a number.")
        try:
            number = float(value)
        except (TypeError, ValueError):
            raise InvalidSetting(f"{key} must be a number.") from None
        if number != number or not 0 <= number <= MAX_NUMERIC_SETTING:
            raise InvalidSetting(f"{key} must be between 0 and {MAX_NUMERIC_SETTING}.")
        if value_type == "int" and number != int(number):
            raise InvalidSetting(f"{key} must be a whole number.")
        return value
    if value_type == "str":
        if not isinstance(value, (str, int, float)) or isinstance(value, bool):
            raise InvalidSetting(f"{key} must be text.")
        text_value = str(value)
        if len(text_value) > MAX_TEXT_SETTING_LENGTH:
            raise InvalidSetting(
                f"{key} is longer than {MAX_TEXT_SETTING_LENGTH} characters."
            )
        if key.endswith("_url"):
            scheme = re.match(r"^\s*([A-Za-z][A-Za-z0-9+.\-]*):", text_value)
            if scheme and scheme.group(1).lower() not in ("http", "https"):
                raise InvalidSetting(f"{key} must be an http(s) link.")
    return value


def set_value(db: Session, key: str, value: Any, *, actor_id=None) -> AppSetting:
    validate_value(key, value)
    value_type = DEFAULTS.get(key, ("str", None))[0]
    row = db.get(AppSetting, key)
    stored = json.dumps(value) if value_type == "json" else str(value)

    if row is None:
        row = AppSetting(key=key, value=stored, value_type=value_type)
        db.add(row)
    else:
        row.value = stored
    row.updated_by_user_id = actor_id
    row.updated_at = utcnow()
    db.flush()
    return row


def get_many(db: Session, keys: list[str]) -> dict[str, Any]:
    """`get` for several keys in ONE query.

    `get` goes through `db.get`, whose identity map holds rows only weakly, so
    asking for five keys in a row was five SELECTs - and the feedback screens
    ask for the same five several times per request.
    """
    rows = {
        row.key: row
        for row in db.execute(select(AppSetting).where(AppSetting.key.in_(keys))).scalars()
    }
    out: dict[str, Any] = {}
    for key in keys:
        value_type, fallback = DEFAULTS.get(key, ("str", None))
        row = rows.get(key)
        out[key] = _coerce(
            row.value if row else None, row.value_type if row else value_type, fallback
        )
    return out


_FEEDBACK_CONFIG_KEYS = {
    "scale_max": "feedback.rating_scale_max",
    "threshold": "feedback.alert_threshold",
    "min_responses": "feedback.alert_min_responses",
    "window_days": "feedback.alert_window_days",
    "expires_days": "feedback.request_expires_days",
}


def feedback_config(db: Session) -> dict[str, Any]:
    values = get_many(db, list(_FEEDBACK_CONFIG_KEYS.values()))
    return {name: values[key] for name, key in _FEEDBACK_CONFIG_KEYS.items()}
