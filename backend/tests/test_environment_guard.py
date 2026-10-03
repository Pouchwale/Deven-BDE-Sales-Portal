"""Local development and production stay apart.

The live portal runs on Render with its own PostgreSQL; this PC has a
separate development copy. These pin the rules that keep commands from
crossing over: development conveniences only touch a local database with
ENV=development set on purpose, and a remote database is only changed with
explicit production configuration.
"""
from __future__ import annotations

import pytest
from sqlalchemy import select

from app.core.config import Settings
from app.core.constants import Role
from app.core.environment_guard import (
    EnvironmentRefused,
    deliberate_target_problems,
    local_development_problems,
    require_local_development,
)
from app.core.security import verify_password
from app.models.org import User

LOCAL_PG = "postgresql+psycopg2://dev:devpw@localhost:5432/bde_portal"
REMOTE_PG = "postgresql+psycopg2://prod:prodpw@dpg-example.oregon-postgres.render.com/prod"


def _settings(monkeypatch, url: str, env: str | None, *, render: bool = False) -> Settings:
    monkeypatch.delenv("ENV", raising=False)
    if render:
        monkeypatch.setenv("RENDER", "true")
    else:
        monkeypatch.delenv("RENDER", raising=False)
    kwargs = {"DATABASE_URL": url, "SECRET_KEY": "x" * 40}
    if env is not None:
        kwargs["ENV"] = env
    return Settings(_env_file=None, **kwargs)


# ------------------------------------------------- local development guard
def test_local_development_is_allowed(monkeypatch) -> None:
    config = _settings(monkeypatch, LOCAL_PG, "development")
    assert config.is_local_development
    assert local_development_problems(config) == []


def test_a_missing_env_is_never_permission(monkeypatch) -> None:
    """ENV still defaults to development for reading, but nothing that
    changes data may rely on a default."""
    config = _settings(monkeypatch, LOCAL_PG, None)
    assert config.ENV == "development"
    assert not config.env_explicit
    assert not config.is_local_development
    with pytest.raises(EnvironmentRefused) as refused:
        require_local_development("seed", config=config)
    assert "ENV is not set" in str(refused.value)


def test_render_is_never_local_development(monkeypatch) -> None:
    """Render runs the live portal - today with ENV=development."""
    config = _settings(monkeypatch, LOCAL_PG, "development", render=True)
    assert not config.is_local_development
    assert any("Render" in p for p in local_development_problems(config))


def test_a_remote_database_is_never_local_development(monkeypatch) -> None:
    config = _settings(monkeypatch, REMOTE_PG, "development")
    assert not config.database_is_local
    problems = local_development_problems(config)
    assert any("not on this machine" in p for p in problems)
    # The refusal names the setting, never the URL or its password.
    assert all("prodpw" not in p and "render.com" not in p for p in problems)


@pytest.mark.parametrize("url", ["sqlite:///x.db", "postgresql://u:p@127.0.0.1/db", LOCAL_PG])
def test_local_databases_are_recognised(monkeypatch, url) -> None:
    assert _settings(monkeypatch, url, "development").database_is_local


# ---------------------------------------------------- remote = production
def test_a_remote_database_needs_explicit_production(monkeypatch) -> None:
    assert deliberate_target_problems(_settings(monkeypatch, REMOTE_PG, "development"))
    assert deliberate_target_problems(_settings(monkeypatch, REMOTE_PG, None))
    assert deliberate_target_problems(_settings(monkeypatch, REMOTE_PG, "production")) == []
    # On Render itself the live database is the intended target.
    assert deliberate_target_problems(_settings(monkeypatch, REMOTE_PG, None, render=True)) == []
    # Local work is never blocked by this guard.
    assert deliberate_target_problems(_settings(monkeypatch, LOCAL_PG, "development")) == []


def test_migrate_upgrade_refuses_a_stray_remote_url(monkeypatch) -> None:
    from app.core import environment_guard
    from app.db import migrate

    config = _settings(monkeypatch, REMOTE_PG, "development")
    monkeypatch.setattr(environment_guard, "default_settings", config)
    monkeypatch.setattr("app.core.config.settings", config)
    with pytest.raises(EnvironmentRefused):
        migrate.main(["upgrade"])


def test_the_seed_refuses_outside_local_development(monkeypatch) -> None:
    from app.core import environment_guard
    from app.seeds import seed

    config = _settings(monkeypatch, LOCAL_PG, "development", render=True)
    monkeypatch.setattr(environment_guard, "default_settings", config)
    monkeypatch.setattr(seed, "settings", config)
    with pytest.raises(EnvironmentRefused):
        seed.main(["--skip-sap"])


# ------------------------------------------------------- dev accounts
def test_dev_accounts_cover_every_role_and_survive_a_rerun(db) -> None:
    from app.seeds import dev_accounts

    created = dev_accounts.ensure(db, "Dev Pass 123")
    db.commit()
    assert set(created.values()) == {"created"}

    users = {
        u.username: u
        for u in db.execute(select(User).where(User.username.like("%-dev"))).scalars()
    }
    assert {u.role for u in users.values()} == {
        Role.SUPER_ADMIN, Role.ADMIN, Role.MANAGER, Role.BDE,
    }
    assert users["parth-dev"].manager_id == users["navya-dev"].id
    assert verify_password("Dev Pass 123", users["navya-dev"].hashed_password)

    # A password changed while testing is NOT undone by a re-run...
    from app.core import password_vault

    password_vault.set_password(users["navya-dev"], "Changed Locally 9")
    db.commit()
    assert set(dev_accounts.ensure(db, "Dev Pass 123").values()) == {"kept"}
    assert verify_password("Changed Locally 9", users["navya-dev"].hashed_password)

    # ...only by an explicit --reset.
    assert dev_accounts.ensure(db, "Dev Pass 123", reset=True)["navya-dev"] == "reset"
    assert verify_password("Dev Pass 123", users["navya-dev"].hashed_password)


def test_dev_accounts_sign_in(client, db) -> None:
    from app.seeds import dev_accounts

    dev_accounts.ensure(db, "Dev Pass 123")
    db.commit()
    response = client.post(
        "/api/auth/login", json={"identifier": "navya-dev", "password": "Dev Pass 123"}
    )
    assert response.status_code == 200, response.text


# ---------------------------------- local .env never resets a copied admin
def test_local_env_values_never_reset_an_existing_super_admin(db, monkeypatch) -> None:
    """A fresh copy of production arrives with production's passwords; the
    PC's own SUPER_ADMIN_* must not overwrite them on the next start."""
    from app.core import password_vault
    from app.core.config import Settings as SettingsClass
    from app.core.config import settings
    from app.services import super_admin_env

    owner = db.execute(
        select(User).where(User.role == Role.SUPER_ADMIN, User.is_active.is_(True))
    ).scalars().first()
    password_vault.set_password(owner, "From Production 4")
    super_admin_env._remember_applied(db, "someone-else", "other values")
    db.flush()

    monkeypatch.setattr(settings, "SUPER_ADMIN_USERNAME", owner.username)
    monkeypatch.setattr(settings, "SUPER_ADMIN_PASSWORD", "Local Env Value 5")
    monkeypatch.setattr(SettingsClass, "is_local_development", property(lambda self: True))

    assert super_admin_env.sync(db) == "kept"
    assert verify_password("From Production 4", owner.hashed_password)
    # And the next start does not try again.
    assert super_admin_env.sync(db) == "unchanged"


def test_a_blank_env_is_the_same_as_a_missing_one(monkeypatch) -> None:
    monkeypatch.setenv("ENV", "")
    monkeypatch.delenv("RENDER", raising=False)
    config = Settings(_env_file=None, DATABASE_URL=LOCAL_PG, SECRET_KEY="x" * 40)
    assert config.ENV == "development"
    assert not config.env_explicit
    assert not config.is_local_development


def test_local_passwords_cover_real_accounts_only(client, db) -> None:
    from app.seeds import dev_accounts

    dev_accounts.ensure(db, "Dev Pass 123")
    db.commit()
    changed = dev_accounts.set_local_passwords(db, "{username}-Local9")
    db.commit()
    assert changed and not any(name.endswith("-dev") for name in changed)

    name = changed[0]
    signed_in = client.post(
        "/api/auth/login", json={"identifier": name, "password": f"{name}-Local9"}
    )
    assert signed_in.status_code == 200, signed_in.text
    # The dev accounts keep their own password.
    dev = client.post(
        "/api/auth/login", json={"identifier": "navya-dev", "password": "Dev Pass 123"}
    )
    assert dev.status_code == 200, dev.text
