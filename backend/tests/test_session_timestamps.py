# pyright: reportArgumentType=false
# (duck-typed stand-ins for Session / UserSession / User, on purpose)
"""PostgreSQL returns timestamptz columns as AWARE datetimes; SQLite and a
freshly-created object give naive UTC. Session and lockout checks compare
stored values with `utcnow()` in Python, and must work with both.

Regression: on PostgreSQL every authenticated request after sign-in failed
with "can't compare offset-naive and offset-aware datetimes" (HTTP 500),
which SQLite-backed tests could not see.
"""
from __future__ import annotations

from datetime import timedelta, timezone
from types import SimpleNamespace

from app.core import sessions
from app.db.base import as_naive_utc, utcnow
from app.services import users as user_service


class _Result:
    def __init__(self, row):
        self._row = row

    def scalar_one_or_none(self):
        return self._row


class _FakeDb:
    def __init__(self, row):
        self._row = row
        self.flushed = False

    def execute(self, _statement):
        return _Result(self._row)

    def flush(self):
        self.flushed = True


def _aware(dt):
    return dt.replace(tzinfo=timezone.utc)


def test_as_naive_utc_normalises_aware_and_keeps_naive():
    naive = utcnow()
    assert as_naive_utc(naive) is naive
    assert as_naive_utc(_aware(naive)) == naive
    ist = timezone(timedelta(hours=5, minutes=30))
    assert as_naive_utc(_aware(naive).astimezone(ist)) == naive


def test_timestamp_type_reads_aware_values_back_as_naive_utc():
    """What psycopg2 returns for timestamptz must come out like SQLite's."""
    from app.db.base import TimestampType

    naive = utcnow()
    ist = timezone(timedelta(hours=5, minutes=30))
    read = TimestampType.process_result_value(_aware(naive).astimezone(ist), None)
    assert read == naive and read.tzinfo is None
    assert TimestampType.process_result_value(naive, None) is naive
    assert TimestampType.process_result_value(None, None) is None
    # And mixing a freshly-created (naive) value with a re-read one works.
    assert max(naive, read) == naive
    bound = TimestampType.process_bind_param(_aware(naive), None)
    assert bound == naive and bound.tzinfo is None


def _row(**overrides):
    now = utcnow()
    values = dict(
        revoked_at=None,
        expires_at=_aware(now + timedelta(hours=1)),
        last_seen_at=_aware(now - timedelta(minutes=5)),
    )
    values.update(overrides)
    return SimpleNamespace(**values)


def test_find_active_session_accepts_aware_timestamps():
    row = _row()
    assert sessions.find_active_session(_FakeDb(row), "secret") is row


def test_find_active_session_rejects_expired_aware_timestamp():
    row = _row(expires_at=_aware(utcnow() - timedelta(seconds=1)))
    assert sessions.find_active_session(_FakeDb(row), "secret") is None


def test_touch_accepts_aware_last_seen():
    row = _row()
    db = _FakeDb(row)
    assert sessions.touch(db, row) is True
    assert db.flushed


def test_is_locked_accepts_aware_locked_until():
    locked = SimpleNamespace(locked_until=_aware(utcnow() + timedelta(minutes=10)))
    unlocked = SimpleNamespace(locked_until=_aware(utcnow() - timedelta(minutes=10)))
    assert user_service.is_locked(locked) is True
    assert user_service.is_locked(unlocked) is False
