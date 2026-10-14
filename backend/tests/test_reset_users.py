"""The terminal password reset (app/seeds/reset_users.py): it changes only the
database the operator names, a reset is proven after commit, and nothing at
startup puts the old password back."""
from __future__ import annotations

import pytest
from sqlalchemy import select

from app.core.config import Settings, settings
from app.core.constants import Role
from app.core.environment_guard import EnvironmentRefused, named_target_problems
from app.core.security import verify_password
from app.models.org import User
from app.seeds import reset_users
from app.services import super_admin_env

from .conftest import SEED_PASSWORD

LOCAL_PG = "postgresql+psycopg2://dev:devpw@localhost:5432/bde_portal"
REMOTE_PG = "postgresql+psycopg2://prod:prodpw@dpg-example.oregon-postgres.render.com/prod"


def _settings(monkeypatch, url: str, env: str | None) -> Settings:
    monkeypatch.delenv("ENV", raising=False)
    monkeypatch.delenv("RENDER", raising=False)
    kwargs = {"DATABASE_URL": url, "SECRET_KEY": "x" * 40}
    if env is not None:
        kwargs["ENV"] = env
    return Settings(_env_file=None, **kwargs)


def _login(client, identifier: str, password: str):
    return client.post("/api/auth/login", json={"identifier": identifier, "password": password})


# ------------------------------------------------------- the named target
def test_local_target_accepts_only_the_local_development_database(monkeypatch) -> None:
    assert named_target_problems("local", _settings(monkeypatch, LOCAL_PG, "development")) == []
    assert named_target_problems("local", _settings(monkeypatch, REMOTE_PG, "development"))
    assert named_target_problems("local", _settings(monkeypatch, REMOTE_PG, "production"))


def test_production_target_never_reaches_this_machine(monkeypatch) -> None:
    assert named_target_problems("production", _settings(monkeypatch, REMOTE_PG, "production")) == []
    # A remote database still needs ENV=production said out loud.
    assert named_target_problems("production", _settings(monkeypatch, REMOTE_PG, None))
    problems = named_target_problems("production", _settings(monkeypatch, LOCAL_PG, "production"))
    assert any("on this machine" in p for p in problems)


def test_the_target_must_be_named(capsys) -> None:
    with pytest.raises(SystemExit) as exc:
        reset_users.main(["--users", "anyone", "--verify-only"])
    assert exc.value.code == 2
    assert "--target" in capsys.readouterr().err


def test_a_production_run_refuses_a_local_database() -> None:
    assert settings.database_is_local  # the test database
    with pytest.raises(EnvironmentRefused):
        reset_users.main(["--target", "production", "--users", "anyone", "--verify-only"])


# ------------------------------------------------- reset, verify, persist
def test_reset_is_proven_after_commit_and_only_the_new_password_signs_in(client, db, users) -> None:
    parth = users["Parth Fulvani"]
    outcome = reset_users.reset_one(db, parth, "Reset Test Value 1", force_change=False)
    assert outcome.found and outcome.updated

    assert _login(client, parth.username, SEED_PASSWORD).status_code == 401
    assert _login(client, parth.username, "Reset Test Value 1").status_code == 200

    checked = reset_users.verify(db, [parth.username], {parth.username: "Reset Test Value 1"})
    assert [o.updated for o in checked] == [True]
    checked = reset_users.verify(db, [parth.username], {parth.username: SEED_PASSWORD})
    assert [o.updated for o in checked] == [False]


def test_verify_only_changes_nothing(db, users) -> None:
    navya = users["Navya Rupawat"]
    before = (navya.hashed_password, navya.password_changed_at)
    reset_users.verify(db, [navya.username], {navya.username: "Not Her Password 1"})
    db.refresh(navya)
    assert (navya.hashed_password, navya.password_changed_at) == before


def test_a_restart_does_not_undo_a_reset_super_admin_password(client, db, monkeypatch) -> None:
    """The Super Admin is the one account startup may touch (SUPER_ADMIN_*).
    With the env values unchanged since they were last applied, a restart -
    or a sign-in typing those old values - must leave a reset password as it
    is."""
    owner = db.execute(
        select(User).where(User.role == Role.SUPER_ADMIN, User.is_active.is_(True))
    ).scalars().first()
    monkeypatch.setattr(settings, "SUPER_ADMIN_USERNAME", owner.username)
    monkeypatch.setattr(settings, "SUPER_ADMIN_PASSWORD", "Env Bootstrap 1")
    super_admin_env._remember_applied(db, owner.username, "Env Bootstrap 1")

    reset_users.reset_one(db, owner, "Reset Test Value 2", force_change=False)
    assert super_admin_env.sync(db) == "unchanged"  # what every start runs
    db.commit()

    assert _login(client, owner.username, "Env Bootstrap 1").status_code == 401
    assert _login(client, owner.username, "Reset Test Value 2").status_code == 200
    db.refresh(owner)
    assert verify_password("Reset Test Value 2", owner.hashed_password)
