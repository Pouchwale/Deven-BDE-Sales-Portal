"""Application configuration, read once from the environment."""
from __future__ import annotations

from functools import cached_property
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parents[2]
REPO_ROOT = BACKEND_DIR.parent
DATA_DIR = REPO_ROOT / "data"


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
    ENV: str = "dev"
    TIMEZONE: str = "Asia/Kolkata"

    DATABASE_URL: str = f"sqlite:///{BACKEND_DIR / 'bde_portal.db'}"

    SECRET_KEY: str = "dev-only-insecure-key-change-me"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 720
    JWT_ALGORITHM: str = "HS256"

    # Kept as a raw string: pydantic-settings JSON-decodes list-typed
    # fields before any validator runs, so a plain comma-separated value
    # in .env would fail to parse. Read it through `cors_origins`.
    CORS_ORIGINS: str = "http://localhost:3000"

    # An optional pattern alongside the exact list, for origins whose host is
    # not known ahead of time - a phone hitting this machine over Wi-Fi, where
    # the address is whatever the router handed out today. Blank disables it,
    # which is the right setting in production: there, the origin IS known.
    CORS_ORIGIN_REGEX: str = ""

    SEED_PASSWORD: str = "ChangeMe@123"
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


settings = Settings()
