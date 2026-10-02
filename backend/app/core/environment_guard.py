"""Which database may a command change?

The live portal runs on Render against its own PostgreSQL; this PC runs a
separate development copy. Every command that creates, resets or replaces
data calls one of these first and stops with a plain explanation when the
target is not the one it is meant for. Fail closed: when in doubt, refuse.

    require_local_development   seeds, development accounts, password resets
                                for the whole roster, sample data. Needs
                                ENV set (not defaulted) to an allowed value,
                                not running on Render, and a database on this
                                machine.

    require_deliberate_target   commands that may legitimately run against
                                production (migrations, the one-off admin and
                                password tools). Anything local is fine; a
                                remote database is only accepted on Render
                                itself, or with ENV=production set explicitly
                                - never because ENV happened to be missing.

Messages name settings, never values: DATABASE_URL carries a password.
"""
from __future__ import annotations

from collections.abc import Iterable

from app.core.config import Settings, settings as default_settings


class EnvironmentRefused(SystemExit):
    """Raised (as a SystemExit, so a CLI stops with exit code 2) when a
    command is pointed at the wrong environment."""

    def __init__(self, operation: str, problems: list[str]) -> None:
        lines = "\n".join(f"  - {p}" for p in problems)
        super().__init__(f"Refusing to run {operation}:\n{lines}")
        self.problems = problems


def local_development_problems(
    config: Settings, allowed_envs: Iterable[str] = ("development",)
) -> list[str]:
    allowed = tuple(allowed_envs)
    problems: list[str] = []
    if not config.env_explicit:
        problems.append(
            "ENV is not set. Put ENV=development in backend/.env for local work "
            "(a missing ENV is never taken as permission)."
        )
    elif config.ENV not in allowed:
        problems.append(f"ENV is {config.ENV}; this only runs with ENV={' or '.join(allowed)}")
    if config.on_render:
        problems.append("this process is running on Render (production)")
    if not config.database_is_local:
        problems.append(
            "DATABASE_URL points at a database that is not on this machine - "
            "development commands only run against local PostgreSQL or SQLite"
        )
    return problems


def require_local_development(
    operation: str,
    allowed_envs: Iterable[str] = ("development",),
    config: Settings | None = None,
) -> None:
    problems = local_development_problems(config or default_settings, allowed_envs)
    if problems:
        raise EnvironmentRefused(operation, problems)


def deliberate_target_problems(config: Settings) -> list[str]:
    if config.database_is_local or config.on_render:
        return []
    if config.ENV == "production" and config.env_explicit:
        return []
    return [
        "DATABASE_URL points at a remote database (production?) but ENV=production "
        "is not explicitly set. Production commands need explicit production "
        "configuration; local work uses the local database in backend/.env."
    ]


def require_deliberate_target(operation: str, config: Settings | None = None) -> None:
    problems = deliberate_target_problems(config or default_settings)
    if problems:
        raise EnvironmentRefused(operation, problems)
