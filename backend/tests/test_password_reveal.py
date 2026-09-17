"""The Super Admin's "show password" (business request).

Sign-in still verifies only the bcrypt hash. The revealable copy is encrypted
at rest, readable by the Super Admin alone, one person per request, and every
reveal is audited without the password in it.
"""
from __future__ import annotations

from sqlalchemy import select

from app.core import password_vault
from app.core.config import Settings
from app.models.org import User
from app.models.system import AuditEvent
from tests.conftest import SEED_PASSWORD, new_email, sign_in, super_admin_headers


def reveal(client, headers, user_id):
    return client.get(f"/api/users/{user_id}/password", headers=headers)


def test_super_admin_sees_a_seeded_password(client, users) -> None:
    response = reveal(client, super_admin_headers(client), users["Parth Fulvani"].id)
    assert response.status_code == 200, response.text
    assert response.json() == {"password": SEED_PASSWORD}


def test_reveal_follows_every_way_a_password_changes(client, users, db) -> None:
    headers = super_admin_headers(client)

    created = client.post(
        "/api/users",
        headers=headers,
        json={"name": "Vault Test", "email": new_email(), "password": "abc", "role": "BDE"},
    )
    assert created.status_code == 201, created.text
    new_id = created.json()["id"]
    assert reveal(client, headers, new_id).json()["password"] == "abc"

    reset = client.post(
        f"/api/users/{new_id}/reset-password",
        headers=headers,
        json={"new_password": "second one", "must_change": False},
    )
    assert reset.status_code == 200, reset.text
    assert reveal(client, headers, new_id).json()["password"] == "second one"

    muskan = users["Muskan Makhija"]
    own = client.post(
        "/api/auth/change-password",
        headers=sign_in(client, muskan),
        json={"old_password": SEED_PASSWORD, "new_password": "my own"},
    )
    assert own.status_code == 200, own.text
    assert reveal(client, super_admin_headers(client), muskan.id).json()["password"] == "my own"


def test_stored_copy_is_encrypted_not_plaintext(client, users, db) -> None:
    user = db.execute(select(User).where(User.email == users["Parth Fulvani"].email)).scalar_one()
    assert user.password_encrypted
    assert SEED_PASSWORD not in user.password_encrypted
    assert password_vault.decrypt(user.password_encrypted) == SEED_PASSWORD


def test_only_the_super_admin_may_reveal(client, users) -> None:
    target = users["Parth Fulvani"].id
    for name in ("Shail Patel", "Navya Rupawat", "Muskan Makhija"):
        response = reveal(client, sign_in(client, users[name]), target)
        assert response.status_code in (403, 404), (name, response.text)
        assert SEED_PASSWORD not in response.text


def test_listings_never_carry_the_password(client, users) -> None:
    headers = super_admin_headers(client)
    for path in ("/api/users?page_size=200", f"/api/users/{users['Parth Fulvani'].id}"):
        body = client.get(path, headers=headers).text
        assert "password_encrypted" not in body
        assert SEED_PASSWORD not in body


def test_every_reveal_is_audited_without_the_password(client, users, db) -> None:
    target = users["Shivani Patel"]
    assert reveal(client, super_admin_headers(client), target.id).status_code == 200
    event = db.execute(
        select(AuditEvent)
        .where(AuditEvent.action == "PASSWORD_VIEWED", AuditEvent.entity_id == target.id)
        .order_by(AuditEvent.created_at.desc())
    ).scalars().first()
    assert event is not None
    assert event.after == {"available": True}
    assert SEED_PASSWORD not in str(event.before) + str(event.after)


def test_missing_copy_reveals_nothing(client, users, db) -> None:
    target = db.execute(
        select(User).where(User.email == users["Bimal"].email)
    ).scalar_one()
    target.password_encrypted = None
    db.commit()
    response = reveal(client, super_admin_headers(client), target.id)
    assert response.status_code == 200
    assert response.json() == {"password": None}


def test_production_refuses_an_invalid_view_key() -> None:
    settings = Settings(
        _env_file=None,
        ENV="production",
        DATABASE_URL="postgresql+psycopg2://u:p@db/x",
        PASSWORD_VIEW_KEY="not-a-fernet-key",
    )
    assert "PASSWORD_VIEW_KEY is not a valid Fernet key" in settings.production_problems()
