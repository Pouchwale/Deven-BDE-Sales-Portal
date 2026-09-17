"""Server-side sessions, cookies, CSRF, lockout and client IP handling."""
from __future__ import annotations

from datetime import timedelta

from sqlalchemy import select
from starlette.requests import Request

from app.core import deps
from app.core.config import settings
from app.core.sessions import hash_token, revoke_user_sessions
from app.db.base import utcnow
from app.models.org import UserSession
from app.models.system import AuditEvent
from tests.conftest import (
    SEED_PASSWORD,
    auth,
    browser_login,
    session_secret,
    sign_in,
    token_for,
)

GENERIC = "Incorrect username or password."


def _login(client, email, password=SEED_PASSWORD, **kwargs):
    return client.post(
        "/api/auth/login", json={"email": email, "password": password}, **kwargs
    )


def _row(db, secret: str) -> UserSession:
    return db.execute(
        select(UserSession).where(UserSession.token_hash == hash_token(secret))
    ).scalar_one()


def _message(response) -> str:
    return response.json()["error"]["message"]


def _set_cookies(response) -> dict[str, str]:
    found = {}
    for header in response.headers.get_list("set-cookie"):
        name = header.split("=", 1)[0]
        found[name] = header
    return found


# ------------------------------------------------------------------ login
def test_login_sets_httponly_session_cookie_and_readable_csrf_cookie(client, users) -> None:
    response = _login(client, users["Shail Patel"].email)
    assert response.status_code == 200, response.text

    cookies = _set_cookies(response)
    session_cookie = cookies[settings.SESSION_COOKIE_NAME]
    csrf_cookie = cookies[settings.CSRF_COOKIE_NAME]

    lowered = session_cookie.lower()
    assert "httponly" in lowered
    assert "path=/" in lowered
    assert f"samesite={settings.COOKIE_SAMESITE.lower()}" in lowered
    assert f"max-age={settings.SESSION_ABSOLUTE_TIMEOUT_MINUTES * 60}" in lowered
    assert ("secure" in lowered.replace("samesite", "")) is settings.COOKIE_SECURE

    assert "httponly" not in csrf_cookie.lower()
    assert response.json()["csrf_token"] == response.cookies.get(settings.CSRF_COOKIE_NAME)


def test_login_body_carries_no_secret_hash_or_password(client, users) -> None:
    response = _login(client, users["Shail Patel"].email)
    secret = session_secret(response)
    body = response.text

    assert set(response.json()) == {"must_change_password", "user", "csrf_token"}
    assert secret not in body
    assert "access_token" not in body
    assert "hashed_password" not in body
    assert "$2b$" not in body
    assert SEED_PASSWORD not in body


def test_the_database_stores_only_a_hash_of_the_secret(client, db, users) -> None:
    secret = token_for(client, users["Shail Patel"].email)
    row = _row(db, secret)
    assert row.token_hash != secret
    assert row.user_id == users["Shail Patel"].id


def test_successful_login_records_bookkeeping_and_audit(client, db, users) -> None:
    user = users["Shail Patel"]
    user.failed_login_count = 3
    db.commit()

    assert _login(client, user.email).status_code == 200
    db.refresh(user)
    assert user.failed_login_count == 0
    assert user.last_login_at is not None
    assert user.last_login_ip == "testclient"
    assert db.execute(
        select(AuditEvent).where(
            AuditEvent.action == "LOGIN_SUCCEEDED", AuditEvent.entity_id == user.id
        )
    ).scalars().first()


def test_wrong_password_unknown_email_and_inactive_share_one_answer(client, db, users) -> None:
    wrong = _login(client, users["Shail Patel"].email, "Not-The-Password-1")
    unknown = _login(client, "nobody-here@pouchwale.com", "Not-The-Password-1")

    inactive_user = users["Parth Fulvani"]
    inactive_user.is_active = False
    db.commit()
    inactive = _login(client, inactive_user.email)  # correct password

    for response in (wrong, unknown, inactive):
        assert response.status_code == 401
        assert _message(response) == GENERIC
        assert settings.SESSION_COOKIE_NAME not in _set_cookies(response)


def test_failed_login_is_audited_without_the_password_or_email(client, db) -> None:
    before = db.query(AuditEvent).filter(AuditEvent.action == "LOGIN_FAILED").count()
    _login(client, "ghost.account@pouchwale.com", "Secret-Attempt-99")
    events = (
        db.query(AuditEvent).filter(AuditEvent.action == "LOGIN_FAILED").all()
    )
    assert len(events) == before + 1
    event = [e for e in events if e.actor_user_id is None][-1]
    assert event.after == {"reason": "bad_credentials"}
    assert "Secret-Attempt-99" not in str(event.after)
    assert "ghost.account" not in str(event.after)


def test_lockout_after_threshold_with_the_same_message(client, db, users) -> None:
    user = users["Parth Fulvani"]
    for _ in range(settings.LOGIN_LOCKOUT_THRESHOLD):
        response = _login(client, user.email, "Wrong-Password-123")
        assert response.status_code == 401

    db.refresh(user)
    assert user.locked_until is not None and user.locked_until > utcnow()
    assert db.execute(
        select(AuditEvent).where(
            AuditEvent.action == "LOGIN_LOCKED", AuditEvent.entity_id == user.id
        )
    ).scalars().first()

    # Even the right password is refused, with the answer anybody gets.
    locked = _login(client, user.email)
    assert locked.status_code == 401
    assert _message(locked) == GENERIC

    # Once the lock lapses the right password works and the counters reset.
    user.locked_until = utcnow() - timedelta(seconds=1)
    db.commit()
    assert _login(client, user.email).status_code == 200
    db.refresh(user)
    assert user.locked_until is None and user.failed_login_count == 0


def test_rate_limiter_answer_is_identical_for_unknown_accounts(client, users) -> None:
    def hammer(email):
        last = None
        for _ in range(settings.LOGIN_RATE_LIMIT_ATTEMPTS + 1):
            last = _login(client, email, "Wrong-Password-123")
        return last

    real = hammer(users["Shail Patel"].email)
    fake = hammer("not-a-person@pouchwale.com")
    assert real.status_code == fake.status_code == 429
    assert _message(real) == _message(fake)


# ------------------------------------------------------------ cookie auth
def test_cookie_authenticates_safe_requests(client, users) -> None:
    cookie, _ = browser_login(client, users["Shail Patel"].email)
    response = client.get("/api/auth/me", headers=cookie)
    assert response.status_code == 200
    assert response.json()["email"] == users["Shail Patel"].email


def test_cookie_post_without_csrf_is_refused(client, db, users) -> None:
    users["Shail Patel"].must_change_password = False
    db.commit()
    cookie, csrf = browser_login(client, users["Shail Patel"].email)

    missing = client.post("/api/notifications/read-all", headers=cookie)
    assert missing.status_code == 403
    assert missing.json()["error"]["code"] == "CSRF_FAILED"

    forged = client.post(
        "/api/notifications/read-all", headers={**cookie, "X-CSRF-Token": "0" * 64}
    )
    assert forged.status_code == 403

    ok = client.post("/api/notifications/read-all", headers={**cookie, **csrf})
    assert ok.status_code == 200, ok.text


def test_csrf_token_from_another_session_is_refused(client, db, users) -> None:
    users["Shail Patel"].must_change_password = False
    db.commit()
    cookie_a, _ = browser_login(client, users["Shail Patel"].email)
    _, csrf_b = browser_login(client, users["Shail Patel"].email)

    # Attacker plants their own CSRF cookie and header alongside the victim's
    # session: the value is not bound to the victim's session, so it fails.
    session_part = cookie_a["Cookie"].split(";")[0]
    planted = {
        "Cookie": f"{session_part}; {settings.CSRF_COOKIE_NAME}={csrf_b['X-CSRF-Token']}",
        **csrf_b,
    }
    response = client.post("/api/notifications/read-all", headers=planted)
    assert response.status_code == 403


def test_bearer_post_needs_no_csrf(client, users) -> None:
    headers = sign_in(client, users["Shail Patel"])
    response = client.post("/api/notifications/read-all", headers=headers)
    assert response.status_code == 200, response.text


# ----------------------------------------------------------------- logout
def test_logout_revokes_the_cookie_session_and_clears_cookies(client, db, users) -> None:
    cookie, csrf = browser_login(client, users["Shail Patel"].email)

    assert client.post("/api/auth/logout", headers=cookie).status_code == 403

    response = client.post("/api/auth/logout", headers={**cookie, **csrf})
    assert response.status_code == 200
    cleared = _set_cookies(response)
    assert settings.SESSION_COOKIE_NAME in cleared
    assert settings.CSRF_COOKIE_NAME in cleared
    assert 'max-age=0' in cleared[settings.SESSION_COOKIE_NAME].lower()

    assert client.get("/api/auth/me", headers=cookie).status_code == 401
    # Idempotent.
    assert client.post("/api/auth/logout", headers={**cookie, **csrf}).status_code == 200
    assert client.post("/api/auth/logout").status_code == 200

    assert db.execute(
        select(AuditEvent).where(
            AuditEvent.action == "LOGOUT", AuditEvent.entity_id == users["Shail Patel"].id
        )
    ).scalars().first()


def test_logout_revokes_a_bearer_session(client, db, users) -> None:
    secret = token_for(client, users["Shail Patel"].email)
    assert client.post("/api/auth/logout", headers=auth(secret)).status_code == 200
    assert client.get("/api/auth/me", headers=auth(secret)).status_code == 401
    assert _row(db, secret).revoked_reason == "LOGOUT"


def test_logout_ends_only_that_session(client, users) -> None:
    first = token_for(client, users["Shail Patel"].email)
    second = token_for(client, users["Shail Patel"].email)
    client.post("/api/auth/logout", headers=auth(first))
    assert client.get("/api/auth/me", headers=auth(second)).status_code == 200


# --------------------------------------------------------- password change
def test_password_change_revokes_every_session(client, users) -> None:
    user = users["Muskan Makhija"]
    one = token_for(client, user.email)
    two = token_for(client, user.email)

    changed = client.post(
        "/api/auth/change-password",
        headers=auth(one),
        json={
            "old_password": SEED_PASSWORD,
            "new_password": "Harbour-Lantern-58",
            "confirm_password": "Harbour-Lantern-58",
        },
    )
    assert changed.status_code == 200, changed.text
    assert "sign in again" in changed.json()["message"].lower()
    assert settings.SESSION_COOKIE_NAME in _set_cookies(changed)

    assert client.get("/api/auth/me", headers=auth(one)).status_code == 401
    assert client.get("/api/auth/me", headers=auth(two)).status_code == 401
    assert _login(client, user.email, "Harbour-Lantern-58").status_code == 200


def test_password_change_applies_policy_and_confirmation(client, users) -> None:
    headers = auth(token_for(client, users["Muskan Makhija"].email))

    empty = client.post(
        "/api/auth/change-password",
        headers=headers,
        json={"old_password": SEED_PASSWORD, "new_password": ""},
    )
    assert empty.status_code == 422

    mismatch = client.post(
        "/api/auth/change-password",
        headers=headers,
        json={
            "old_password": SEED_PASSWORD,
            "new_password": "Harbour-Lantern-58",
            "confirm_password": "Harbour-Lantern-59",
        },
    )
    assert mismatch.status_code == 422
    assert "Harbour" not in mismatch.text

    # Nothing changed, so the session is still good.
    assert client.get("/api/auth/me", headers=headers).status_code == 200


def test_admin_password_reset_revokes_the_targets_sessions(client, users) -> None:
    target = users["Parth Fulvani"]
    target_session = token_for(client, target.email)
    admin = sign_in(client, users["Shail Patel"])

    reset = client.post(
        f"/api/users/{target.id}/reset-password",
        headers=admin,
        json={"new_password": "Copper-Meadow-4821", "must_change": True},
    )
    assert reset.status_code == 200, reset.text
    assert client.get("/api/auth/me", headers=auth(target_session)).status_code == 401
    # The admin's own session is untouched.
    assert client.get("/api/auth/me", headers=admin).status_code == 200


def test_revoke_user_sessions_directly(client, db, users) -> None:
    user = users["Shail Patel"]
    one = token_for(client, user.email)
    two = token_for(client, user.email)
    keep = _row(db, two).id

    ended = revoke_user_sessions(db, user.id, "ADMIN", except_session_id=keep)
    db.commit()
    assert ended >= 1
    assert client.get("/api/auth/me", headers=auth(one)).status_code == 401
    assert client.get("/api/auth/me", headers=auth(two)).status_code == 200


def test_session_predating_a_password_change_is_dead(client, db, users) -> None:
    user = users["Shail Patel"]
    secret = token_for(client, user.email)
    user.password_changed_at = utcnow() + timedelta(seconds=1)
    db.commit()
    assert client.get("/api/auth/me", headers=auth(secret)).status_code == 401


def test_deactivated_user_session_is_refused(client, db, users) -> None:
    user = users["Parth Fulvani"]
    secret = token_for(client, user.email)
    user.is_active = False
    db.commit()
    assert client.get("/api/auth/me", headers=auth(secret)).status_code == 401


# ----------------------------------------------------------------- expiry
def test_idle_session_expires(client, db, users) -> None:
    secret = token_for(client, users["Shail Patel"].email)
    row = _row(db, secret)
    row.last_seen_at = utcnow() - timedelta(minutes=settings.SESSION_IDLE_TIMEOUT_MINUTES + 1)
    db.commit()
    assert client.get("/api/auth/me", headers=auth(secret)).status_code == 401


def test_activity_slides_the_idle_window(client, db, users) -> None:
    secret = token_for(client, users["Shail Patel"].email)
    row = _row(db, secret)
    stale = utcnow() - timedelta(minutes=settings.SESSION_IDLE_TIMEOUT_MINUTES - 5)
    row.last_seen_at = stale
    db.commit()
    assert client.get("/api/auth/me", headers=auth(secret)).status_code == 200
    db.refresh(row)
    assert row.last_seen_at > stale + timedelta(minutes=1)


def test_absolute_expiry_wins_over_activity(client, db, users) -> None:
    secret = token_for(client, users["Shail Patel"].email)
    row = _row(db, secret)
    row.expires_at = utcnow() - timedelta(seconds=1)
    db.commit()
    assert client.get("/api/auth/me", headers=auth(secret)).status_code == 401


def test_garbage_credentials_are_401(client) -> None:
    assert client.get("/api/auth/me", headers=auth("not-a-session")).status_code == 401
    assert client.get("/api/auth/me", headers={"Authorization": "Basic abc"}).status_code == 401
    assert (
        client.get(
            "/api/auth/me", headers={"Cookie": f"{settings.SESSION_COOKIE_NAME}=nope"}
        ).status_code
        == 401
    )


# -------------------------------------------------------------- client ip
def _request(peer: str, forwarded: str | None = None) -> Request:
    headers = []
    if forwarded is not None:
        headers.append((b"x-forwarded-for", forwarded.encode()))
    return Request({"type": "http", "client": (peer, 5555), "headers": headers})


def test_forwarded_for_is_ignored_from_untrusted_peers(monkeypatch) -> None:
    monkeypatch.setattr(settings, "TRUSTED_PROXIES", "")
    assert deps.client_ip(_request("203.0.113.9", "1.2.3.4")) == "203.0.113.9"

    monkeypatch.setattr(settings, "TRUSTED_PROXIES", "127.0.0.1")
    assert deps.client_ip(_request("203.0.113.9", "1.2.3.4")) == "203.0.113.9"


def test_forwarded_for_is_honoured_from_trusted_proxies(monkeypatch) -> None:
    monkeypatch.setattr(settings, "TRUSTED_PROXIES", "127.0.0.1, 10.0.0.0/8")
    assert deps.client_ip(_request("127.0.0.1", "198.51.100.7")) == "198.51.100.7"
    # A client-supplied spoof on the left is skipped: the rightmost untrusted
    # hop is the one our proxy actually saw.
    assert (
        deps.client_ip(_request("127.0.0.1", "6.6.6.6, 198.51.100.7, 10.1.2.3"))
        == "198.51.100.7"
    )
    assert deps.client_ip(_request("127.0.0.1")) == "127.0.0.1"
    assert deps.client_ip(_request("127.0.0.1", "not-an-ip")) == "127.0.0.1"


def test_spoofed_forwarded_for_does_not_reach_the_audit_trail(client, db, users) -> None:
    user = users["Shail Patel"]
    response = _login(client, user.email, headers={"X-Forwarded-For": "6.6.6.6"})
    assert response.status_code == 200
    db.refresh(user)
    assert user.last_login_ip != "6.6.6.6"
