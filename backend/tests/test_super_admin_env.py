"""The Super Admin's username and password come from the env file."""
from __future__ import annotations

from sqlalchemy import select

from app.core import password_vault
from app.core.config import settings
from app.core.constants import Role
from app.core.security import verify_password
from app.models.org import User
from app.services import super_admin_env


def _owner(db) -> User:
    return db.execute(
        select(User).where(User.role == Role.SUPER_ADMIN, User.is_active.is_(True))
    ).scalars().first()


def _configure(monkeypatch, username: str | None, password: str, **extra: str) -> None:
    monkeypatch.setattr(settings, "SUPER_ADMIN_USERNAME", username or "")
    monkeypatch.setattr(settings, "SUPER_ADMIN_PASSWORD", password)
    for key, value in extra.items():
        monkeypatch.setattr(settings, key, value)


def test_nothing_happens_when_not_configured(db, monkeypatch) -> None:
    _configure(monkeypatch, "", "")
    assert super_admin_env.sync(db) == "not-configured"


def test_env_password_becomes_the_super_admin_password(client, db, monkeypatch) -> None:
    owner = _owner(db)
    _configure(monkeypatch, owner.username, "From The Env 1")
    assert super_admin_env.sync(db) == "updated"
    db.commit()

    assert verify_password("From The Env 1", owner.hashed_password)
    assert password_vault.decrypt(owner.password_encrypted) == "From The Env 1"
    signed_in = client.post(
        "/api/auth/login", json={"identifier": owner.username, "password": "From The Env 1"}
    )
    assert signed_in.status_code == 200, signed_in.text

    # A second start with the same env changes nothing.
    assert super_admin_env.sync(db) == "unchanged"


def test_env_username_renames_the_only_super_admin(db, monkeypatch) -> None:
    owner = _owner(db)
    _configure(monkeypatch, "Portal Boss", "Boss Pass 9")
    assert super_admin_env.sync(db) == "updated"
    assert owner.username == "portalboss"


def test_lock_and_deactivation_are_cleared(db, monkeypatch) -> None:
    from app.db.base import utcnow

    owner = _owner(db)
    _configure(monkeypatch, owner.username, "Unlock Me 5")
    super_admin_env.sync(db)
    owner.locked_until = utcnow()
    owner.failed_login_count = 4
    assert super_admin_env.sync(db) == "updated"
    assert owner.locked_until is None and owner.failed_login_count == 0


def test_username_of_another_role_is_refused(db, users, monkeypatch) -> None:
    _configure(monkeypatch, users["Parth Fulvani"].username, "Hijack 1")
    before = users["Parth Fulvani"].hashed_password
    assert super_admin_env.sync(db) == "username-taken"
    assert users["Parth Fulvani"].hashed_password == before
    assert users["Parth Fulvani"].role != Role.SUPER_ADMIN


def test_creates_a_super_admin_when_none_exists(db, monkeypatch) -> None:
    for admin in db.execute(select(User).where(User.role == Role.SUPER_ADMIN)).scalars():
        admin.is_active = False
        admin.username = None
    db.flush()
    _configure(
        monkeypatch,
        "rootadmin",
        "Fresh Start 3",
        SUPER_ADMIN_EMAIL="root@example.com",
        SUPER_ADMIN_NAME="Root Admin",
    )
    assert super_admin_env.sync(db) == "created"
    created = db.execute(select(User).where(User.username == "rootadmin")).scalar_one()
    assert created.role == Role.SUPER_ADMIN and created.is_active
    assert verify_password("Fresh Start 3", created.hashed_password)


def test_create_works_without_an_email(db, monkeypatch) -> None:
    for admin in db.execute(select(User).where(User.role == Role.SUPER_ADMIN)).scalars():
        admin.is_active = False
        admin.username = None
    db.flush()
    _configure(monkeypatch, "rootadmin", "Fresh Start 3", SUPER_ADMIN_EMAIL="")
    assert super_admin_env.sync(db) == "created"
    created = db.execute(select(User).where(User.username == "rootadmin")).scalar_one()
    assert created.email == "rootadmin@pouchwale.com"


def test_quotes_and_spaces_around_env_values_are_ignored(db, monkeypatch) -> None:
    owner = _owner(db)
    _configure(monkeypatch, f"  {owner.username} ", '"Quoted Pass 7" ')
    assert super_admin_env.sync(db) == "updated"
    assert verify_password("Quoted Pass 7", owner.hashed_password)


def _login(client, identifier, password):
    return client.post("/api/auth/login", json={"identifier": identifier, "password": password})


def test_sign_in_with_env_credentials_on_an_empty_database(client, db, monkeypatch) -> None:
    """A fresh hosted database: nobody synced at startup, the first sign-in
    with the env credentials creates the Super Admin."""
    for admin in db.execute(select(User).where(User.role == Role.SUPER_ADMIN)).scalars():
        admin.is_active = False
        admin.username = None
    db.commit()
    _configure(monkeypatch, "superadmin", "ChangeMe@123", SUPER_ADMIN_EMAIL="")
    response = _login(client, "superadmin", "ChangeMe@123")
    assert response.status_code == 200, response.text
    assert response.json()["user"]["role"] == Role.SUPER_ADMIN


def test_env_credentials_get_past_a_lockout(client, db, monkeypatch) -> None:
    from app.db.base import utcnow
    from datetime import timedelta

    owner = _owner(db)
    _configure(monkeypatch, owner.username, "Env Pass 11")
    super_admin_env.sync(db)
    owner.locked_until = utcnow() + timedelta(minutes=15)
    db.commit()
    assert _login(client, owner.username, "Env Pass 11").status_code == 200


def test_wrong_password_for_the_env_username_is_still_refused(client, db, monkeypatch) -> None:
    owner = _owner(db)
    _configure(monkeypatch, owner.username, "Env Pass 11")
    super_admin_env.sync(db)
    db.commit()
    response = _login(client, owner.username, "not it")
    assert response.status_code == 401
    assert verify_password("Env Pass 11", owner.hashed_password)
