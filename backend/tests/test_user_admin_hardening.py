"""User administration hardening: validation, password resets, lockout,
deactivation, last-Super-Admin protection and the per-user activity list.

Every route here is exercised through the API, the way the admin screen uses
it. test_user_management.py covers the authority rules; this file covers the
production-safety behaviour layered on top of them.
"""
from __future__ import annotations

import json
import uuid
from datetime import timedelta

import pytest
from sqlalchemy import func, select

from app.core.constants import ErrorCode, Role
from app.core.errors import ApiError
from app.db.base import utcnow
from app.models.org import Team, User, UserSession
from app.models.system import AuditEvent, Notification
from app.services import users as user_service
from tests.conftest import (
    SEED_PASSWORD,
    auth,
    new_email,
    sign_in,
    super_admin_headers,
    token_for,
)

GOOD_PASSWORD = "Harbour7Lantern"


def error_code(response) -> str:
    return response.json()["error"]["code"]


def create_body(**overrides) -> dict:
    body = {
        "name": "Fresh Hire",
        "email": new_email(),
        "password": GOOD_PASSWORD,
        "role": "BDE",
    }
    body.update(overrides)
    return body


def login(client, email: str, password: str):
    return client.post("/api/auth/login", json={"email": email, "password": password})


# ------------------------------------------------------------- creation
def test_create_rejects_a_duplicate_email_in_any_case(client, users) -> None:
    headers = sign_in(client, users["Shail Patel"])
    taken = users["Muskan Makhija"].email
    response = client.post(
        "/api/users", headers=headers, json=create_body(email=f"  {taken.title()}  ".strip())
    )
    assert response.status_code == 409, response.text
    assert "already in use" in response.json()["error"]["message"]


def test_create_normalises_the_email_to_lowercase(client, users) -> None:
    headers = super_admin_headers(client)
    mixed = f"Mixed.Case-{uuid.uuid4().hex[:6]}@Example.COM"
    response = client.post("/api/users", headers=headers, json=create_body(email=mixed))
    assert response.status_code == 201, response.text
    assert response.json()["email"] == mixed.lower()


def test_create_rejects_a_nonexistent_team(client, users) -> None:
    response = client.post(
        "/api/users",
        headers=super_admin_headers(client),
        json=create_body(team_id=str(uuid.uuid4())),
    )
    assert response.status_code == 422, response.text
    assert "team" in response.json()["error"]["message"].lower()


def test_create_rejects_an_inactive_team(client, users, db) -> None:
    team = Team(name="Retired Team", code="RETIRED", is_active=False, sort_order=99)
    db.add(team)
    db.commit()
    response = client.post(
        "/api/users",
        headers=super_admin_headers(client),
        json=create_body(team_id=str(team.id)),
    )
    assert response.status_code == 422, response.text


def test_create_rejects_a_nonexistent_manager(client, users) -> None:
    response = client.post(
        "/api/users",
        headers=super_admin_headers(client),
        json=create_body(manager_id=str(uuid.uuid4())),
    )
    assert response.status_code == 422, response.text


def test_create_rejects_an_inactive_manager(client, users, db) -> None:
    manager = users["Navya Rupawat"]
    manager.is_active = False
    db.commit()
    response = client.post(
        "/api/users",
        headers=super_admin_headers(client),
        json=create_body(manager_id=str(manager.id)),
    )
    assert response.status_code == 422, response.text
    assert "deactivated" in response.json()["error"]["message"].lower()


@pytest.mark.parametrize(
    "simple",
    ["abc", "short1", "onlyletterslong", "1234567890123", "Password123"],
)
def test_create_accepts_any_non_empty_password(client, users, simple) -> None:
    """No length or character rules: the business chose to allow any password."""
    body = create_body(password=simple, email=new_email())
    response = client.post("/api/users", headers=super_admin_headers(client), json=body)
    assert response.status_code == 201, response.text
    assert simple not in response.text
    assert login(client, body["email"], simple).status_code == 200


@pytest.mark.parametrize("unusable", ["", "x" * 73])
def test_create_rejects_an_empty_or_over_long_password(client, users, unusable) -> None:
    response = client.post(
        "/api/users", headers=super_admin_headers(client), json=create_body(password=unusable)
    )
    assert response.status_code == 422, response.text


def test_create_rejects_mismatched_confirmation(client, users) -> None:
    response = client.post(
        "/api/users",
        headers=super_admin_headers(client),
        json=create_body(confirm_password="Something9Else"),
    )
    assert response.status_code == 422, response.text


def test_create_can_skip_the_forced_change(client, users) -> None:
    email = new_email()
    response = client.post(
        "/api/users",
        headers=super_admin_headers(client),
        json=create_body(email=email, confirm_password=GOOD_PASSWORD, must_change_password=False),
    )
    assert response.status_code == 201, response.text
    assert response.json()["must_change_password"] is False
    signed = login(client, email, GOOD_PASSWORD)
    assert signed.status_code == 200
    assert signed.json()["must_change_password"] is False


def test_create_defaults_to_forcing_a_change(client, users) -> None:
    response = client.post(
        "/api/users", headers=super_admin_headers(client), json=create_body()
    )
    assert response.status_code == 201
    assert response.json()["must_change_password"] is True


# --------------------------------------------------------------- editing
def test_edit_email_persists_and_the_new_email_signs_in(client, users, db) -> None:
    target = users["Aastha Ramchandani"]
    old_email = target.email
    fresh = new_email().upper()
    response = client.patch(
        f"/api/users/{target.id}", headers=super_admin_headers(client), json={"email": fresh}
    )
    assert response.status_code == 200, response.text
    assert response.json()["email"] == fresh.lower()
    db.expire_all()
    assert db.get(User, target.id).email == fresh.lower()
    assert login(client, old_email, SEED_PASSWORD).status_code == 401
    assert login(client, fresh.lower(), SEED_PASSWORD).status_code == 200


def test_edit_rejects_moving_to_an_inactive_team(client, users, db) -> None:
    team = Team(name="Closed Team", code="CLOSED", is_active=False, sort_order=98)
    db.add(team)
    db.commit()
    response = client.patch(
        f"/api/users/{users['Parth Fulvani'].id}",
        headers=super_admin_headers(client),
        json={"team_id": str(team.id)},
    )
    assert response.status_code == 422, response.text


def test_edit_rejects_self_as_manager(client, users) -> None:
    target = users["Parth Fulvani"]
    response = client.patch(
        f"/api/users/{target.id}",
        headers=super_admin_headers(client),
        json={"manager_id": str(target.id)},
    )
    assert response.status_code in (409, 422), response.text


def test_role_change_ends_the_targets_sessions(client, users) -> None:
    target = users["Parth Fulvani"]
    their_headers = sign_in(client, target)
    assert client.get("/api/auth/me", headers=their_headers).status_code == 200
    response = client.patch(
        f"/api/users/{target.id}",
        headers=sign_in(client, users["Shail Patel"]),
        json={"role": "SALES"},
    )
    assert response.status_code == 200, response.text
    assert client.get("/api/auth/me", headers=their_headers).status_code == 401


# ------------------------------------------------------- password reset
def test_reset_password_old_fails_new_works_and_sessions_end(client, users, db) -> None:
    target = users["Shivani Patel"]
    their_headers = sign_in(client, target)
    assert client.get("/api/auth/me", headers=their_headers).status_code == 200

    response = client.post(
        f"/api/users/{target.id}/reset-password",
        headers=sign_in(client, users["Shail Patel"]),
        json={
            "new_password": GOOD_PASSWORD,
            "confirm_password": GOOD_PASSWORD,
            "must_change": True,
        },
    )
    assert response.status_code == 200, response.text
    assert GOOD_PASSWORD not in response.text

    assert client.get("/api/auth/me", headers=their_headers).status_code == 401
    live = db.scalar(
        select(func.count(UserSession.id)).where(
            UserSession.user_id == target.id, UserSession.revoked_at.is_(None)
        )
    )
    assert live == 0

    assert login(client, target.email, SEED_PASSWORD).status_code == 401
    fresh = login(client, target.email, GOOD_PASSWORD)
    assert fresh.status_code == 200
    assert fresh.json()["must_change_password"] is True


def test_reset_password_rejects_mismatch_and_empty(client, users) -> None:
    target = users["Shivani Patel"]
    headers = sign_in(client, users["Shail Patel"])
    mismatch = client.post(
        f"/api/users/{target.id}/reset-password",
        headers=headers,
        json={"new_password": GOOD_PASSWORD, "confirm_password": "Different9Words"},
    )
    assert mismatch.status_code == 422
    empty = client.post(
        f"/api/users/{target.id}/reset-password",
        headers=headers,
        json={"new_password": ""},
    )
    assert empty.status_code == 422
    # Neither attempt changed anything.
    assert login(client, target.email, SEED_PASSWORD).status_code == 200


def test_reset_password_clears_a_lockout(client, users, db) -> None:
    target = users["Shivani Patel"]
    target.failed_login_count = 7
    target.locked_until = utcnow() + timedelta(hours=1)
    db.commit()
    response = client.post(
        f"/api/users/{target.id}/reset-password",
        headers=sign_in(client, users["Shail Patel"]),
        json={"new_password": GOOD_PASSWORD},
    )
    assert response.status_code == 200, response.text
    db.expire_all()
    stored = db.get(User, target.id)
    assert stored.failed_login_count == 0
    assert stored.locked_until is None


# ---------------------------------------------------------- deactivation
def test_deactivate_blocks_login_ends_sessions_and_keeps_history(client, users, db) -> None:
    from app.models.lead import Lead

    target = users["Parth Fulvani"]
    manager = sign_in(client, users["Navya Rupawat"])
    lead = client.post(
        "/api/leads",
        headers=manager,
        json={
            "name": "History Keeper Ltd",
            "mobile": "9800000999",
            "assigned_to_user_id": str(target.id),
        },
    )
    assert lead.status_code in (200, 201), lead.text
    lead_id = uuid.UUID(lead.json()["id"])

    their_headers = sign_in(client, target)
    audits_before = db.scalar(
        select(func.count(AuditEvent.id)).where(
            (AuditEvent.actor_user_id == target.id) | (AuditEvent.entity_id == target.id)
        )
    )
    notes_before = db.scalar(
        select(func.count(Notification.id)).where(Notification.user_id == target.id)
    )

    response = client.delete(
        f"/api/users/{target.id}", headers=sign_in(client, users["Shail Patel"])
    )
    assert response.status_code == 200, response.text

    assert client.get("/api/auth/me", headers=their_headers).status_code == 401
    assert login(client, target.email, SEED_PASSWORD).status_code == 401

    db.expire_all()
    assert db.get(User, target.id) is not None
    assert db.get(Lead, lead_id).assigned_to_user_id == target.id
    assert db.scalar(
        select(func.count(AuditEvent.id)).where(
            (AuditEvent.actor_user_id == target.id) | (AuditEvent.entity_id == target.id)
        )
    ) >= audits_before
    assert db.scalar(
        select(func.count(Notification.id)).where(Notification.user_id == target.id)
    ) == notes_before


def test_reactivate_restores_sign_in(client, users) -> None:
    target = users["Bimal"]
    headers = sign_in(client, users["Shail Patel"])
    assert client.delete(f"/api/users/{target.id}", headers=headers).status_code == 200
    assert login(client, target.email, SEED_PASSWORD).status_code == 401

    response = client.post(f"/api/users/{target.id}/reactivate", headers=headers)
    assert response.status_code == 200, response.text
    assert response.json()["is_active"] is True
    assert login(client, target.email, SEED_PASSWORD).status_code == 200


# ------------------------------------------------ last Super Admin guard
def test_last_super_admin_cannot_be_deactivated_demoted_or_deleted(client, users) -> None:
    owner = users["Portal Owner"]
    headers = super_admin_headers(client)

    deactivate = client.delete(f"/api/users/{owner.id}", headers=headers)
    assert deactivate.status_code == 409, deactivate.text
    assert "last active Super Admin" in deactivate.json()["error"]["message"]

    demote = client.patch(f"/api/users/{owner.id}", headers=headers, json={"role": "ADMIN"})
    assert demote.status_code == 409, demote.text

    patch_off = client.patch(
        f"/api/users/{owner.id}", headers=headers, json={"is_active": False}
    )
    assert patch_off.status_code == 409, patch_off.text

    delete = client.delete(f"/api/users/{owner.id}/permanent", headers=headers)
    assert delete.status_code == 409, delete.text

    assert owner.is_active is True
    assert owner.role == Role.SUPER_ADMIN


def test_last_super_admin_guard_applies_even_with_authority(db, users) -> None:
    """With a second Super Admin in place the first may be managed; once only
    one active Super Admin remains, it is refused at the service layer too."""
    from app.core.security import hash_password

    owner = users["Portal Owner"]
    backup = User(
        name="Backup Owner",
        email=new_email(),
        role=Role.SUPER_ADMIN,
        hashed_password=hash_password(GOOD_PASSWORD),
        manager_id=owner.id,
        is_active=True,
    )
    db.add(backup)
    db.flush()

    # Two active: the owner (backup's manager) may deactivate the backup.
    user_service.deactivate_user(db, owner, backup)
    assert backup.is_active is False

    # Now the owner is the only one left.
    with pytest.raises(ApiError) as refused:
        user_service.deactivate_user(db, backup, owner)
    assert refused.value.status_code == 409


# ---------------------------------------------------------------- unlock
def test_unlock_clears_the_lock_and_is_audited(client, users, db) -> None:
    target = users["Muskan Makhija"]
    target.failed_login_count = 9
    target.locked_until = utcnow() + timedelta(minutes=30)
    db.commit()

    headers = super_admin_headers(client)
    detail = client.get(f"/api/users/{target.id}", headers=headers).json()
    assert detail["is_locked"] is True
    assert detail["failed_login_count"] == 9

    response = client.post(f"/api/users/{target.id}/unlock", headers=headers)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["is_locked"] is False
    assert body["failed_login_count"] == 0
    assert body["locked_until"] is None

    actions = {
        row.action
        for row in db.execute(
            select(AuditEvent).where(AuditEvent.entity_id == target.id)
        ).scalars()
    }
    assert "ACCOUNT_UNLOCKED" in actions


# -------------------------------------------------------------- activity
def test_activity_lists_events_about_and_by_the_user_without_secrets(client, users) -> None:
    target = users["Shivani Patel"]
    headers = super_admin_headers(client)
    secret = "Lighthouse42Beam"
    client.post(
        f"/api/users/{target.id}/reset-password",
        headers=headers,
        json={"new_password": secret},
    )
    client.patch(f"/api/users/{target.id}", headers=headers, json={"title": "Senior BDE"})

    response = client.get(
        f"/api/users/{target.id}/activity", headers=headers, params={"page_size": 5}
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["page_size"] == 5
    assert body["total"] >= 2
    actions = {item["action"] for item in body["items"]}
    assert {"PASSWORD_RESET", "USER_UPDATED"} <= actions
    for item in body["items"]:
        assert item["entity_id"] == str(target.id) or item["actor_user_id"] == str(target.id)
    for leak in (secret, "hashed_password", "plain_password", "$2b$", '"password"'):
        assert leak not in response.text


def test_activity_page_size_is_bounded(client, users) -> None:
    response = client.get(
        f"/api/users/{users['Parth Fulvani'].id}/activity",
        headers=super_admin_headers(client),
        params={"page_size": 1000},
    )
    assert response.status_code == 422


def test_activity_is_scoped(client, users) -> None:
    """An Admin cannot read the Super Admin's activity; a manager cannot read
    anyone's; an unknown id is a 404."""
    admin = sign_in(client, users["Shail Patel"])
    assert client.get(
        f"/api/users/{users['Portal Owner'].id}/activity", headers=admin
    ).status_code == 403
    assert client.get(
        f"/api/users/{users['Parth Fulvani'].id}/activity", headers=admin
    ).status_code == 200
    assert client.get(
        f"/api/users/{uuid.uuid4()}/activity", headers=super_admin_headers(client)
    ).status_code == 404


# ------------------------------------------------------- non-admin callers
@pytest.mark.parametrize("caller", ["Navya Rupawat", "Parth Fulvani", "SALES"])
def test_non_admins_are_refused_every_admin_action(client, users, caller) -> None:
    if caller == "SALES":
        email = new_email()
        created = client.post(
            "/api/users",
            headers=super_admin_headers(client),
            json=create_body(
                email=email, role="SALES", must_change_password=False, name="Sales Person"
            ),
        )
        assert created.status_code == 201, created.text
        headers = auth(token_for(client, email, GOOD_PASSWORD))
    else:
        headers = sign_in(client, users[caller])

    target = users["Shivani Patel"]
    checks = [
        client.post("/api/users", headers=headers, json=create_body()),
        client.post(
            f"/api/users/{target.id}/reset-password",
            headers=headers,
            json={"new_password": GOOD_PASSWORD},
        ),
        client.delete(f"/api/users/{target.id}", headers=headers),
        client.post(f"/api/users/{target.id}/unlock", headers=headers),
        client.get(f"/api/users/{target.id}/activity", headers=headers),
    ]
    for response in checks:
        assert response.status_code == 403, f"{response.request.url}: {response.text}"


# ------------------------------------------------ no password in responses
def _walk_keys(value) -> set[str]:
    keys: set[str] = set()
    if isinstance(value, dict):
        for key, inner in value.items():
            keys.add(key)
            keys |= _walk_keys(inner)
    elif isinstance(value, list):
        for inner in value:
            keys |= _walk_keys(inner)
    return keys


def test_no_users_endpoint_returns_password_material(client, users) -> None:
    headers = super_admin_headers(client)
    target = users["Parth Fulvani"]
    created = client.post("/api/users", headers=headers, json=create_body())
    responses = [
        created,
        client.get("/api/users", headers=headers, params={"page_size": 200, "include_inactive": True}),
        client.get(f"/api/users/{target.id}", headers=headers),
        client.get("/api/users/actionable", headers=headers),
        client.get("/api/users/org-chart", headers=headers),
        client.patch(f"/api/users/{target.id}", headers=headers, json={"title": "BDE"}),
        client.post(
            f"/api/users/{target.id}/reset-password",
            headers=headers,
            json={"new_password": GOOD_PASSWORD},
        ),
        client.post(f"/api/users/{target.id}/unlock", headers=headers),
        client.get(f"/api/users/{target.id}/activity", headers=headers),
        client.get("/api/auth/me", headers=headers),
    ]
    for response in responses:
        assert response.status_code < 400, f"{response.request.url}: {response.text}"
        keys = _walk_keys(response.json())
        for forbidden_key in ("hashed_password", "plain_password", "password", "new_password"):
            assert forbidden_key not in keys, f"{response.request.url} returned {forbidden_key}"
        text = json.dumps(response.json())
        assert "$2b$" not in text and GOOD_PASSWORD not in text
