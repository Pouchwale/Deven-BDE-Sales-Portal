"""Production configuration: refuses loudly, never leaks a value.

Settings are built directly with `_env_file=None` and explicit arguments, so
neither the developer's backend/.env nor the suite's own environment decides
the outcome.
"""
from __future__ import annotations

import secrets

import pytest

from app.core.config import ConfigError, KNOWN_DEFAULT_SECRETS, Settings, load_settings

GOOD_KEY = secrets.token_urlsafe(48)
DB_PASSWORD = "db-password-that-must-never-appear"


def prod(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "_env_file": None,
        "ENV": "production",
        "DATABASE_URL": f"postgresql+psycopg2://bde_portal:{DB_PASSWORD}@db.internal:5432/bde_portal",
        "SECRET_KEY": GOOD_KEY,
        "CORS_ORIGINS": "https://portal.example.com",
        "CORS_ORIGIN_REGEX": "",
        "COOKIE_SECURE": True,
        "COOKIE_SAMESITE": "lax",
        "SEED_PASSWORD": "",
        "DOCS_ENABLED": False,
        "TRUSTED_PROXIES": "127.0.0.1",
        "SAP_DATA_FILE": "",
        "CHAT_ENABLED": False,
        "GROQ_API_KEY": "",
    }
    values.update(overrides)
    return Settings(**values)  # type: ignore[arg-type]


# ------------------------------------------------------------ the good case
def test_a_complete_production_config_passes() -> None:
    config = prod()
    assert config.is_production
    assert config.production_problems() == []
    config.validate_for_production()  # does not raise
    assert config.docs_enabled is False
    assert config.log_format == "json"
    assert config.production_warnings() == []


def test_load_settings_accepts_a_good_production_config() -> None:
    values = {k: v for k, v in prod().model_dump().items()}
    loaded = load_settings(_env_file=None, **values)
    assert loaded.is_production


# ------------------------------------------------------------ each bad setting
@pytest.mark.parametrize(
    "overrides,fragment",
    [
        ({"DATABASE_URL": "sqlite:///./portal.db"}, "DATABASE_URL must be a PostgreSQL URL"),
        ({"SECRET_KEY": ""}, "SECRET_KEY is not set"),
        ({"SECRET_KEY": "short-key"}, "SECRET_KEY must be at least 32 characters"),
        ({"SECRET_KEY": "change-me-before-deploying"}, "SECRET_KEY is a published default"),
        ({"SECRET_KEY": "dev-only-insecure-key-change-me"}, "SECRET_KEY is a published default"),
        ({"CORS_ORIGINS": ""}, "CORS_ORIGINS must list"),
        ({"CORS_ORIGINS": "*"}, "wildcard"),
        ({"CORS_ORIGINS": "http://portal.example.com"}, "is not https"),
        ({"CORS_ORIGINS": "https://localhost:3000"}, "localhost origin"),
        ({"CORS_ORIGINS": "https://127.0.0.1"}, "localhost origin"),
        ({"CORS_ORIGIN_REGEX": "^https://.*$"}, "CORS_ORIGIN_REGEX must be blank"),
        ({"COOKIE_SECURE": False}, "COOKIE_SECURE must be true"),
        ({"COOKIE_SAMESITE": "none"}, "COOKIE_SAMESITE must be lax or strict"),
        ({"SEED_PASSWORD": "ChangeMe@123"}, "SEED_PASSWORD must not be set"),
    ],
)
def test_each_unsafe_setting_is_refused_by_name(overrides: dict, fragment: str) -> None:
    config = prod(**overrides)
    with pytest.raises(ConfigError) as caught:
        config.validate_for_production()
    message = str(caught.value)
    assert "Refusing to start" in message
    assert fragment in message
    # Names, never values.
    for value in overrides.values():
        if isinstance(value, str) and len(value) > 3:
            assert value not in message
    assert DB_PASSWORD not in message
    assert GOOD_KEY not in message


def test_every_problem_is_listed_at_once() -> None:
    config = prod(
        DATABASE_URL="sqlite://",
        SECRET_KEY="",
        CORS_ORIGINS="http://localhost:3000",
        COOKIE_SECURE=False,
    )
    assert len(config.production_problems()) >= 4


def test_known_defaults_include_every_placeholder_ever_shipped() -> None:
    assert "change-me-before-deploying" in KNOWN_DEFAULT_SECRETS
    assert "dev-only-insecure-key-change-me" in KNOWN_DEFAULT_SECRETS


def test_load_settings_raises_config_error_in_production() -> None:
    values = prod().model_dump()
    values["COOKIE_SECURE"] = False
    with pytest.raises(ConfigError, match="COOKIE_SECURE"):
        load_settings(_env_file=None, **values)


# ------------------------------------------------------------ all environments
def test_database_url_has_no_default_anywhere(monkeypatch) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    with pytest.raises(ConfigError) as caught:
        load_settings(_env_file=None, ENV="development")
    assert "DATABASE_URL" in str(caught.value)


def test_invalid_values_are_reported_without_echoing_the_input(monkeypatch) -> None:
    marker = "this-value-must-not-be-echoed-9f3a"
    with pytest.raises(ConfigError) as caught:
        load_settings(
            _env_file=None,
            ENV="development",
            DATABASE_URL="sqlite://",
            COOKIE_SAMESITE=marker,
        )
    assert "COOKIE_SAMESITE" in str(caught.value)
    assert marker not in str(caught.value)


@pytest.mark.parametrize("raw,expected", [("dev", "development"), ("Development", "development"),
                                          ("test", "test"), ("e2e", "e2e"),
                                          ("PRODUCTION", "production")])
def test_env_names_are_normalised(raw: str, expected: str) -> None:
    config = Settings(_env_file=None, ENV=raw, DATABASE_URL="postgresql://x", SECRET_KEY=GOOD_KEY)
    assert config.ENV == expected


def test_unknown_env_is_refused() -> None:
    with pytest.raises(ConfigError, match="ENV"):
        load_settings(_env_file=None, ENV="staging-ish", DATABASE_URL="sqlite://")


def test_blank_secret_key_outside_production_is_random_not_a_shared_default() -> None:
    a = Settings(_env_file=None, ENV="development", DATABASE_URL="sqlite://", SECRET_KEY="")
    b = Settings(_env_file=None, ENV="development", DATABASE_URL="sqlite://", SECRET_KEY="")
    assert a.secret_key_generated and b.secret_key_generated
    assert len(a.SECRET_KEY) >= 32
    assert a.SECRET_KEY != b.SECRET_KEY
    assert a.SECRET_KEY not in KNOWN_DEFAULT_SECRETS


def test_blank_secret_key_with_a_database_password_survives_a_restart() -> None:
    """A host that sleeps and wakes restarts the process; a random key there
    broke every signed-in person's CSRF token on each wake."""
    url = "postgresql+psycopg2://app:s3cret-pass@db.internal:5432/portal"
    a = Settings(_env_file=None, ENV="development", DATABASE_URL=url, SECRET_KEY="")
    b = Settings(_env_file=None, ENV="development", DATABASE_URL=url, SECRET_KEY="")
    other = Settings(
        _env_file=None,
        ENV="development",
        DATABASE_URL=url.replace("s3cret-pass", "another-pass"),
        SECRET_KEY="",
    )
    assert a.SECRET_KEY == b.SECRET_KEY
    assert a.SECRET_KEY != other.SECRET_KEY
    assert len(a.SECRET_KEY) >= 32 and "s3cret-pass" not in a.SECRET_KEY


def test_development_is_not_validated_as_production() -> None:
    config = Settings(_env_file=None, ENV="development", DATABASE_URL="sqlite://")
    config.validate_for_production()  # no-op outside production
    assert config.docs_enabled is True
    assert config.log_format == "text"


def test_seed_password_has_no_hardcoded_default() -> None:
    assert Settings.model_fields["SEED_PASSWORD"].default == ""


# ------------------------------------------------------------ warnings, not crashes
def test_missing_sap_file_and_chat_key_are_warnings_not_errors(tmp_path) -> None:
    config = prod(
        SAP_DATA_FILE=str(tmp_path / "missing.xlsm"),
        CHAT_ENABLED=True,
        GROQ_API_KEY="",
    )
    config.validate_for_production()  # still starts
    warnings = " ".join(config.production_warnings())
    assert "SAP_DATA_FILE does not exist" in warnings
    assert "GROQ_API_KEY is blank" in warnings
    assert config.chat_enabled is False


def test_relative_sap_path_is_warned() -> None:
    assert any("absolute" in w for w in prod(SAP_DATA_FILE="data/sap.xlsm").production_warnings())


# ------------------------------------------------------------ app shape
def test_docs_are_disabled_in_the_production_app_kwargs() -> None:
    from app.main import fastapi_kwargs

    kwargs = fastapi_kwargs(prod(DOCS_ENABLED=True))
    assert kwargs["docs_url"] is None
    assert kwargs["redoc_url"] is None
    assert kwargs["openapi_url"] is None

    dev = fastapi_kwargs(Settings(_env_file=None, ENV="development", DATABASE_URL="sqlite://"))
    assert dev["docs_url"] == "/docs"
    assert dev["openapi_url"] == "/openapi.json"


def test_cors_is_explicit() -> None:
    from app.main import cors_kwargs

    kwargs = cors_kwargs(prod())
    assert kwargs["allow_origins"] == ["https://portal.example.com"]
    assert kwargs["allow_origin_regex"] is None
    assert kwargs["allow_credentials"] is True
    assert "*" not in kwargs["allow_methods"]
    assert set(kwargs["allow_methods"]) == {"GET", "POST", "PATCH", "PUT", "DELETE", "OPTIONS"}
    assert set(kwargs["allow_headers"]) == {
        "Content-Type", "X-CSRF-Token", "X-Request-ID", "Authorization"
    }
    assert kwargs["expose_headers"] == ["X-Request-ID"]

    no_origins = cors_kwargs(prod(CORS_ORIGINS=""))
    assert no_origins["allow_credentials"] is False


# ------------------------------------------------------------ tooling refusals
def test_seed_refuses_production_without_the_flag(monkeypatch, capsys) -> None:
    from app.core.config import settings
    from app.seeds import seed

    monkeypatch.setattr(settings, "ENV", "production")
    assert seed.main([]) == 1
    assert seed.main(["--reset-passwords", "--force", "--allow-production"]) == 1
    assert "Refusing" in capsys.readouterr().err


def test_seed_never_creates_roster_users_in_production(monkeypatch, db) -> None:
    from app.core.config import settings
    from app.seeds import seed

    monkeypatch.setattr(settings, "ENV", "production")
    with pytest.raises(seed.SeedRefused):
        seed.seed_users(db, {})


def test_seed_requires_seed_password_to_create_users(monkeypatch, db) -> None:
    from app.core.config import settings
    from app.core.constants import Role
    from app.seeds import seed
    from app.seeds.roster import SeedUser

    # Nothing to create: every roster account exists, so no password needed.
    monkeypatch.setattr(settings, "SEED_PASSWORD", "")
    teams = {t.code: t for t in db.query(seed.Team).all()}
    seed.seed_users(db, teams)

    # One account missing: now the password is required.
    monkeypatch.setattr(
        seed, "ROSTER", (*seed.ROSTER, SeedUser("Not Yet Created", Role.BDE, "BDE"))
    )
    with pytest.raises(seed.SeedRefused, match="SEED_PASSWORD"):
        seed.seed_users(db, teams)


def test_sample_feedback_refuses_to_load_in_production(monkeypatch, capsys) -> None:
    from app.core.config import settings
    from app.seeds import sample_feedback

    monkeypatch.setattr(settings, "ENV", "production")
    assert sample_feedback.main([]) == 1
    assert "Refusing" in capsys.readouterr().err


def test_migrate_check_passes_on_the_migrated_test_database(capsys) -> None:
    from app.db import migrate

    assert migrate.main(["check"]) == 0
    assert "up to date" in capsys.readouterr().out
