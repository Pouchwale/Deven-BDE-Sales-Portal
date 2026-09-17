"""The Super Admin's sign-in, owned by the environment.

SUPER_ADMIN_USERNAME and SUPER_ADMIN_PASSWORD in the env file are the source
of truth for the Super Admin account. Every time the backend starts:

* the account with that username (which must be a SUPER_ADMIN) gets that
  password, if it does not already have it - and is unlocked and reactivated;
* if no account has that username yet, the one active Super Admin is renamed
  to it; with none at all, one is created (SUPER_ADMIN_EMAIL optional).

The same happens at sign-in when the env username and password are typed
exactly, so the Super Admin can always get in with them.

Changing the password in the env file and restarting is therefore how the
Super Admin password is changed. Every change is audited; nothing is logged
that contains the password.
"""
from __future__ import annotations

import hmac

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core import password_vault
from app.core.config import settings
from app.core.constants import AuditAction, EntityType, Role
from app.core.logging import get_logger
from app.core.security import verify_password
from app.core.sessions import RevokeReason, revoke_user_sessions
from app.core.usernames import InvalidUsername, validate_username
from app.db.base import utcnow
from app.models.org import User
from app.seeds.roster import EMAIL_DOMAIN
from app.services import audit

log = get_logger("app.super_admin")


def _clean(value: str) -> str:
    """Hosting dashboards and hand-edited env files add stray spaces and
    quotes around values; neither is ever part of the intended value."""
    text = (value or "").strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in "'\"":
        text = text[1:-1]
    return text


def _env_credentials() -> tuple[str, str]:
    return _clean(settings.SUPER_ADMIN_USERNAME), _clean(settings.SUPER_ADMIN_PASSWORD)


def matches_env(login_name: str, password: str) -> bool:
    """Is this sign-in exactly the env Super Admin's username and password?

    Used at sign-in so the env credentials work even if the startup sync did
    not run (or the account was locked since): whoever holds the env password
    is, by definition, the Super Admin.
    """
    raw_username, env_password = _env_credentials()
    if not raw_username or not env_password:
        return False
    try:
        username = validate_username(raw_username)
    except InvalidUsername:
        return False
    return hmac.compare_digest(login_name.encode(), username.encode()) and hmac.compare_digest(
        password.encode(), env_password.encode()
    )


def sync(db: Session) -> str:
    """Apply the env credentials. Returns what happened, for logs and tests.
    Commits nothing - the caller does."""
    raw_username, password = _env_credentials()
    if not raw_username or not password:
        return "not-configured"
    try:
        username = validate_username(raw_username)
    except InvalidUsername:
        log.error("SUPER_ADMIN_USERNAME is not a valid username; Super Admin not synced")
        return "invalid-username"

    user = db.execute(select(User).where(User.username == username)).scalar_one_or_none()

    if user is not None and user.role != Role.SUPER_ADMIN:
        log.error("SUPER_ADMIN_USERNAME belongs to a non-Super-Admin account; not synced")
        return "username-taken"

    if user is None:
        admins = db.execute(
            select(User).where(User.role == Role.SUPER_ADMIN, User.is_active.is_(True))
        ).scalars().all()
        if len(admins) == 1:
            user = admins[0]
            before = {"username": user.username}
            user.username = username
            db.flush()
            audit.record(
                db,
                actor_id=None,
                action=AuditAction.USER_UPDATED,
                entity_type=EntityType.USER,
                entity_id=user.id,
                before=before,
                after={"username": username, "source": "env"},
            )
        elif not admins:
            # Optional: a hosting dashboard often only gets the username and
            # password. The address can be corrected later in User admin.
            email = _clean(settings.SUPER_ADMIN_EMAIL).lower() or f"{username}@{EMAIL_DOMAIN}"
            taken = db.execute(select(User).where(User.email == email)).scalar_one_or_none()
            if taken is not None:
                log.error("SUPER_ADMIN_EMAIL belongs to another account; Super Admin not created")
                return "email-taken"
            user = User(
                name=settings.SUPER_ADMIN_NAME.strip() or "Super Admin",
                email=email,
                username=username,
                role=str(Role.SUPER_ADMIN),
                hashed_password="",
                is_active=True,
                must_change_password=False,
            )
            password_vault.set_password(user, password)
            user.password_changed_at = utcnow()
            db.add(user)
            db.flush()
            audit.record(
                db,
                actor_id=None,
                action=AuditAction.USER_CREATED,
                entity_type=EntityType.USER,
                entity_id=user.id,
                after={"role": Role.SUPER_ADMIN, "username": username, "source": "env"},
            )
            log.info("Super Admin created from the environment")
            return "created"
        else:
            log.error(
                "Several Super Admins exist and none has SUPER_ADMIN_USERNAME; "
                "set it on one of them in User admin"
            )
            return "ambiguous"

    changed = False
    if not verify_password(password, user.hashed_password):
        password_vault.set_password(user, password)
        user.password_changed_at = utcnow()
        revoke_user_sessions(db, user.id, RevokeReason.PASSWORD_RESET)
        changed = True
    elif password_vault.decrypt(user.password_encrypted) != password:
        # Right password, but no readable copy yet - refresh it quietly.
        user.password_encrypted = password_vault.encrypt(password)
    if not user.is_active or user.locked_until is not None or user.must_change_password:
        user.is_active = True
        user.deactivated_at = None
        user.locked_until = None
        user.failed_login_count = 0
        user.must_change_password = False
        changed = True
    db.flush()

    if changed:
        audit.record(
            db,
            actor_id=None,
            action=AuditAction.PASSWORD_RESET,
            entity_type=EntityType.USER,
            entity_id=user.id,
            after={"source": "env"},
        )
        log.info("Super Admin sign-in updated from the environment")
        return "updated"
    return "unchanged"


def sync_on_startup() -> None:
    """Never lets a problem here stop the portal from starting."""
    from app.db.session import SessionLocal

    try:
        with SessionLocal() as db:
            sync(db)
            db.commit()
    except Exception as exc:  # noqa: BLE001 - logged, and startup continues
        log.error("Super Admin env sync failed: %s", type(exc).__name__)
