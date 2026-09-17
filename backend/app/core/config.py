"""Application configuration, read once from the environment.

Environments
    ENV=development   local work (legacy spelling "dev" is accepted)
    ENV=test          the pytest suite
    ENV=e2e           a disposable server the end-to-end runners drive
    ENV=production    real users; `validate_for_production()` must pass

Loading never silently falls back to anything that matters: DATABASE_URL has
no default in any environment, and production refuses to start on an insecure
setting. Problems are reported by setting NAME only - never by value, because
the values are secrets.
"""
from __future__ import annotations

import re
import secrets
from functools import cached_property
from pathlib import Path

from pydantic import ValidationError, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parents[2]
REPO_ROOT = BACKEND_DIR.parent
DATA_DIR = REPO_ROOT / "data"

APP_VERSION = "3.0.0"

ENVIRONMENTS = ("development", "test", "e2e", "production")
_ENV_ALIASES = {"dev": "development", "prod": "production"}

#: Secrets that have ever been written down in this repository or its docs.
#: A key on this list is public knowledge, whatever its length.
KNOWN_DEFAULT_SECRETS = frozenset(
    {
        "dev-only-insecure-key-change-me",
        "change-me-before-deploying",
        "change-me",
        "changeme",
        "change_me",
        "secret",
        "secret-key",
        "test-secret-key-not-used-anywhere-real",
        "replace-with-output-of-secrets-token-urlsafe-48",
    }
)

_LOCAL_HOST = re.compile(
    r"^https?://(localhost|127\.\d+\.\d+\.\d+|0\.0\.0\.0|\[::1\])(:\d+)?/?$",
    re.IGNORECASE,
)


class ConfigError(RuntimeError):
    """The configuration cannot be used. The message names settings, never
    their values."""


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        # backend/.env wins over the repo-root .env, so a developer can keep a
        # private override without touching the shared file.
        env_file=(REPO_ROOT / ".env", BACKEND_DIR / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    APP_NAME: str = "BDE & Sales Portal"
    ENV: str = "development"
    TIMEZONE: str = "Asia/Kolkata"

    # No default, in any environment. A missing URL used to fall back to a
    # SQLite file beside the code, which is how a misconfigured server ends up
    # quietly running on an empty database of its own.
    DATABASE_URL: str

    #: PostgreSQL connection pool (ignored for SQLite).
    DB_POOL_SIZE: int = 5
    DB_MAX_OVERFLOW: int = 10
    DB_POOL_TIMEOUT: int = 30
    DB_POOL_RECYCLE: int = 1800
    #: Server-side cap on any single statement, in ms. 0 disables it. The
    #: migration runner always runs without one.
    DB_STATEMENT_TIMEOUT_MS: int = 30_000

    # Signs CSRF tokens. Required in production. Outside production a blank
    # key is replaced by a random one per process (with a warning), so there
    # is never a shared, hard-coded default.
    SECRET_KEY: str = ""

    # Kept as a raw string: pydantic-settings JSON-decodes list-typed
    # fields before any validator runs, so a plain comma-separated value
    # in .env would fail to parse. Read it through `cors_origins`.
    CORS_ORIGINS: str = "http://localhost:3000"

    # An optional pattern alongside the exact list, for origins whose host is
    # not known ahead of time - a phone hitting this machine over Wi-Fi, where
    # the address is whatever the router handed out today. Blank disables it,
    # which is the right setting in production: there, the origin IS known.
    CORS_ORIGIN_REGEX: str = ""

    #: /docs, /redoc and /openapi.json. Always off in production.
    DOCS_ENABLED: bool = True

    # ----------------------------------------------------------- logging
    LOG_LEVEL: str = "INFO"
    #: "json" or "text". Blank = json in production, text elsewhere.
    LOG_FORMAT: str = ""
    #: Requests slower than this are logged at WARNING.
    SLOW_REQUEST_MS: int = 1500

    # Development / test only, and required only when the seed actually
    # creates accounts. No default: a password written into the code is a
    # password every copy of the code knows.
    SEED_PASSWORD: str = ""
    # Whether seeded accounts must replace the shared password before they can
    # use the portal. Off during development so signing in is one click; the
    # enforcement itself is untouched and turning this on re-arms it for every
    # account on the next `seed --reset-passwords`.
    #
    # Turn this ON before the portal reaches real users: while it is off, every
    # account shares one password that is written down in .env.
    SEED_FORCE_PASSWORD_CHANGE: bool = False

    FEEDBACK_RATING_SCALE_MAX: int = 5
    FEEDBACK_ALERT_THRESHOLD: float = 3.0
    FEEDBACK_ALERT_MIN_RESPONSES: int = 5
    FEEDBACK_ALERT_WINDOW_DAYS: int = 30


    LOGIN_RATE_LIMIT_ATTEMPTS: int = 10
    LOGIN_RATE_LIMIT_WINDOW_SECONDS: int = 300

    # --------------------------------------------------------- sessions
    # Server-managed sessions (app/core/sessions.py), carried in an HttpOnly
    # cookie. Absolute lifetime, and how long an unused session survives.
    SESSION_ABSOLUTE_TIMEOUT_MINUTES: int = 720
    SESSION_IDLE_TIMEOUT_MINUTES: int = 120
    SESSION_COOKIE_NAME: str = "bde_session"
    CSRF_COOKIE_NAME: str = "bde_csrf"
    #: True in production (HTTPS only). Dev over http://localhost needs False.
    COOKIE_SECURE: bool = False
    COOKIE_SAMESITE: str = "lax"
    #: Blank = host-only cookie, which is what a same-origin deployment wants.
    COOKIE_DOMAIN: str = ""

    # Per-account lockout, on top of the per-IP limiter above.
    LOGIN_LOCKOUT_THRESHOLD: int = 5
    LOGIN_LOCKOUT_MINUTES: int = 15

    #: Proxies whose X-Forwarded-For is believed. Blank = trust none, so a
    #: client cannot spoof its IP past the rate limiter.
    TRUSTED_PROXIES: str = ""

    #: Fernet key that encrypts the copy of each password the Super Admin
    #: may reveal (app/core/password_vault.py). Blank = nothing stored or
    #: shown. Generate: python -c "from cryptography.fernet import Fernet;
    #: print(Fernet.generate_key().decode())". Losing it only loses the
    #: reveal - sign-in never uses it.
    PASSWORD_VIEW_KEY: str = ""

    # ----------------------------------------------------------- assistant
    # The in-portal AI assistant, served by Groq. Off unless BOTH the flag is
    # on and a key is present - see `chat_enabled`, which is what the app
    # actually reads.
    GROQ_API_KEY: str = ""
    CHAT_ENABLED: bool = False
    # Production model, 131k context, and one of the models that supports
    # PARALLEL tool calling - which the loop in services/chat/session.py
    # relies on when a question needs two lookups at once.
    GROQ_MODEL: str = "llama-3.3-70b-versatile"
    # An assistant that reports figures should not be inventive. Low, not
    # zero: zero makes it repeat a phrasing verbatim across turns.
    CHAT_TEMPERATURE: float = 0.2
    CHAT_MAX_TOKENS: int = 2048
    # How many prior messages are replayed. Trimming is a cost decision, not a
    # capacity one - the model's context window is far larger than this.
    CHAT_HISTORY_MESSAGES: int = 12
    CHAT_MAX_TOOL_CALLS: int = 6
    # Hard ceiling on rows any single tool may return. The model can ask for
    # fewer; it cannot ask for more.
    CHAT_MAX_ROWS: int = 25
    CHAT_RATE_LIMIT_MESSAGES: int = 20
    CHAT_RATE_LIMIT_WINDOW_SECONDS: int = 300
    CHAT_REQUEST_TIMEOUT_SECONDS: int = 60

    # ------------------------------------------------- google form sync
    # Feedback responses arrive from an Apps Script bound to the company's
    # Google Form. No Google credentials live here - only a shared secret,
    # which is what the script signs its deliveries with.
    #
    # Env, never app_settings: an admin UI that can read a secret back is a
    # secret you have lost.
    GOOGLE_SYNC_SECRET: str = ""
    #: The Apps Script Web App, for the pull fallback. Blank disables it.
    GOOGLE_SYNC_URL: str = ""
    #: How far out of step a delivery's clock may be before it is refused.
    #: Bounds a captured request to this many seconds.
    GOOGLE_SYNC_MAX_SKEW_SECONDS: int = 300
    GOOGLE_SYNC_MAX_BODY_BYTES: int = 65_536
    GOOGLE_SYNC_RATE_LIMIT: int = 60
    GOOGLE_SYNC_RATE_WINDOW_SECONDS: int = 60
    GOOGLE_SYNC_TIMEOUT_SECONDS: int = 30

    #: Shown in the admin setup card. Not a secret, and not user-editable.
    CHAT_PROVIDER: str = "Groq"

    # ------------------------------------------------------------ validation
    @field_validator("ENV", mode="before")
    @classmethod
    def _normalise_env(cls, value: object) -> str:
        text = str(value or "").strip().lower() or "development"
        text = _ENV_ALIASES.get(text, text)
        if text not in ENVIRONMENTS:
            raise ValueError(f"ENV must be one of: {', '.join(ENVIRONMENTS)}")
        return text

    @field_validator("DATABASE_URL", mode="after")
    @classmethod
    def _database_url_present(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("DATABASE_URL is empty")
        return value.strip()

    @field_validator("COOKIE_SAMESITE", mode="after")
    @classmethod
    def _samesite(cls, value: str) -> str:
        text = value.strip().lower()
        if text not in {"lax", "strict", "none"}:
            raise ValueError("COOKIE_SAMESITE must be lax, strict or none")
        return text

    @model_validator(mode="after")
    def _ephemeral_secret_key(self) -> "Settings":
        """Outside production, a blank SECRET_KEY becomes a random per-process
        key. Production is refused instead - see `production_problems`."""
        if not self.SECRET_KEY.strip() and self.ENV != "production":
            self.SECRET_KEY = secrets.token_urlsafe(48)
            self._secret_key_generated = True
        return self

    _secret_key_generated: bool = False

    @property
    def secret_key_generated(self) -> bool:
        return self._secret_key_generated

    @property
    def is_production(self) -> bool:
        return self.ENV == "production"

    @property
    def docs_enabled(self) -> bool:
        return self.DOCS_ENABLED and not self.is_production

    @property
    def log_format(self) -> str:
        fmt = self.LOG_FORMAT.strip().lower()
        if fmt in {"json", "text"}:
            return fmt
        return "json" if self.is_production else "text"

    def production_problems(self) -> list[str]:
        """Every reason this configuration is unsafe for production.

        Names settings and rules only; never includes a value.
        """
        problems: list[str] = []

        url = self.DATABASE_URL.strip().lower()
        if not url.startswith("postgresql"):
            problems.append("DATABASE_URL must be a PostgreSQL URL (SQLite is not allowed)")

        key = self.SECRET_KEY.strip()
        if not key:
            problems.append("SECRET_KEY is not set")
        elif key.lower() in KNOWN_DEFAULT_SECRETS or "change-me" in key.lower():
            problems.append("SECRET_KEY is a published default value")
        elif len(key) < 32:
            problems.append("SECRET_KEY must be at least 32 characters")

        origins = self.cors_origins
        if not origins:
            problems.append("CORS_ORIGINS must list the portal's https origin(s)")
        for index, origin in enumerate(origins, start=1):
            label = f"CORS_ORIGINS entry {index}"
            if origin.strip() == "*" or "*" in origin:
                problems.append(f"{label} is a wildcard")
            elif not origin.lower().startswith("https://"):
                problems.append(f"{label} is not https")
            elif _LOCAL_HOST.match(origin.strip()):
                problems.append(f"{label} is a localhost origin")
        if self.CORS_ORIGIN_REGEX.strip():
            problems.append("CORS_ORIGIN_REGEX must be blank")

        if not self.COOKIE_SECURE:
            problems.append("COOKIE_SECURE must be true")
        if self.COOKIE_SAMESITE == "none":
            problems.append("COOKIE_SAMESITE must be lax or strict")

        if self.PASSWORD_VIEW_KEY.strip():
            try:
                from cryptography.fernet import Fernet

                Fernet(self.PASSWORD_VIEW_KEY.strip().encode("ascii"))
            except Exception:
                problems.append("PASSWORD_VIEW_KEY is not a valid Fernet key")

        if self.SEED_PASSWORD.strip():
            problems.append(
                "SEED_PASSWORD must not be set (shared seed passwords are "
                "development only; create the first admin with "
                "python -m app.seeds.bootstrap_admin)"
            )

        return problems

    def production_warnings(self) -> list[str]:
        """Things worth an error log line in production, but not a refusal."""
        warnings: list[str] = []
        sap = self.SAP_DATA_FILE.strip().strip('"').strip("'")
        if sap:
            path = Path(sap)
            if not path.is_absolute():
                warnings.append("SAP_DATA_FILE should be an absolute path")
            elif not path.exists():
                warnings.append("SAP_DATA_FILE does not exist; SAP sync will find nothing")
        if self.CHAT_ENABLED and not self.GROQ_API_KEY.strip():
            warnings.append("CHAT_ENABLED is on but GROQ_API_KEY is blank; the assistant is disabled")
        if not self.TRUSTED_PROXIES.strip():
            warnings.append(
                "TRUSTED_PROXIES is blank; behind a reverse proxy every client "
                "will share the proxy's IP for rate limiting and audit"
            )
        if self.GOOGLE_SYNC_SECRET.strip() and len(self.GOOGLE_SYNC_SECRET.strip()) < 32:
            warnings.append("GOOGLE_SYNC_SECRET should be at least 32 characters")
        if self.DOCS_ENABLED:
            warnings.append("DOCS_ENABLED is ignored in production; API docs are off")
        return warnings

    def validate_for_production(self) -> None:
        """Raise ConfigError listing every problem, if ENV=production."""
        if not self.is_production:
            return
        problems = self.production_problems()
        if problems:
            raise ConfigError(
                "Refusing to start: the production configuration is unsafe.\n"
                + "\n".join(f"  - {p}" for p in problems)
            )

    @cached_property
    def chat_enabled(self) -> bool:
        """Can the assistant actually answer?

        A flag on its own is not enough - without a key there is nothing to
        call. `CHAT_ENABLED` alone still shows the launcher and a setup panel;
        this decides whether the composer appears.
        """
        return self.CHAT_ENABLED and bool(self.GROQ_API_KEY.strip())

    @cached_property
    def feedback_sync_enabled(self) -> bool:
        """Without a secret there is nothing to verify against, so the
        webhook refuses everything rather than accepting anything."""
        return bool(self.GOOGLE_SYNC_SECRET.strip())

    @cached_property
    def cors_origins(self) -> list[str]:
        """Accept `a,b` or a JSON array from the environment."""
        text = self.CORS_ORIGINS.strip()
        if text.startswith("["):
            import json

            return [str(v) for v in json.loads(text)]
        return [part.strip() for part in text.split(",") if part.strip()]

    @cached_property
    def cors_origin_regex(self) -> str | None:
        """None rather than an empty string: an empty pattern matches every
        origin, which would silently turn the allow-list off."""
        return self.CORS_ORIGIN_REGEX.strip() or None

    @cached_property
    def is_sqlite(self) -> bool:
        return self.DATABASE_URL.startswith("sqlite")

    @cached_property
    def safe_database_url(self) -> str:
        """The URL with any password removed, for logs and CLI output."""
        url = self.DATABASE_URL
        if "://" not in url or "@" not in url:
            return url
        scheme, rest = url.split("://", 1)
        creds, host = rest.rsplit("@", 1)
        user = creds.split(":", 1)[0]
        return f"{scheme}://{user}:***@{host}"

    #: The SAP workbook the portal imports customers from. Blank falls back to
    #: every export dropped into data/sap/.
    SAP_DATA_FILE: str = ""
    #: Give each imported SAP customer a converted lead assigned to its Sales
    #: Person, so it shows in Assigned Leads, Reference Tracking and feedback.
    SAP_LINK_LEADS: bool = True
    #: Watch SAP_DATA_FILE and import every new save automatically.
    SAP_AUTO_SYNC: bool = True
    SAP_SYNC_INTERVAL_SECONDS: int = 30

    @property
    def sap_data_dir(self) -> Path:
        return DATA_DIR / "sap"

    @property
    def sap_data_files(self) -> list[Path]:
        """The SAP exports to import: SAP_DATA_FILE if set, else data/sap/."""
        if self.SAP_DATA_FILE.strip():
            return [Path(self.SAP_DATA_FILE.strip().strip('"'))]
        return sorted(
            p
            for p in self.sap_data_dir.glob("*")
            if p.suffix.lower() in {".csv", ".xlsx", ".xlsm", ".xls"}
        )


def load_settings(**overrides: object) -> Settings:
    """Build and check the settings, failing with a readable message.

    pydantic's own ValidationError text includes the offending input values -
    which here are passwords and keys - so it is rebuilt from locations and
    messages only.
    """
    try:
        loaded = Settings(**overrides)  # type: ignore[arg-type]
    except ValidationError as exc:
        lines = []
        for err in exc.errors():
            name = ".".join(str(p) for p in err.get("loc", ())) or "settings"
            message = "is required" if err.get("type") == "missing" else err.get("msg", "")
            lines.append(f"  - {name}: {message}")
        raise ConfigError(
            "Invalid configuration (set these in the environment or backend/.env):\n"
            + "\n".join(lines)
        ) from None
    loaded.validate_for_production()
    return loaded


settings = load_settings()
