"""Sign in, sign out and the forced password change.

A browser session is a server-side row (app/core/sessions.py) named by an
opaque secret that lives only in an HttpOnly cookie. The JSON bodies here
never carry that secret.
"""
from __future__ import annotations

from datetime import timedelta

from fastapi import APIRouter, Request, Response, status
from sqlalchemy import func, select

from app.core import password_vault
from app.core import sessions as session_store
from app.core.config import settings
from app.core.constants import AuditAction, EntityType, ErrorCode
from app.core.deps import (
    AuthenticatedUser,
    DbSession,
    client_ip,
    csrf_failed,
    csrf_is_valid,
    session_credentials,
)
from app.core.errors import ApiError, invalid, unauthorized
from app.core.passwords import PasswordPolicyError, validate_password
from app.core.ratelimit import SlidingWindowLimiter
from app.core.usernames import normalise_login
from app.core.security import burn_password_check, csrf_token_for, verify_password
from app.db.base import as_naive_utc, utcnow
from app.models.org import User
from app.schemas.common import Message
from app.schemas.user import (
    ChangePasswordRequest,
    LoginRequest,
    TokenResponse,
    UserOut,
)
from app.services import audit, super_admin_env
from app.services import users as user_service

router = APIRouter(prefix="/auth", tags=["auth"])

login_limiter = SlidingWindowLimiter(
    settings.LOGIN_RATE_LIMIT_ATTEMPTS, settings.LOGIN_RATE_LIMIT_WINDOW_SECONDS
)

#: One message for "no such account", "wrong password", "deactivated" and
#: "locked", so the endpoint cannot be used to enumerate staff.
BAD_CREDENTIALS = "Incorrect username or password."


# ----------------------------------------------------------------- cookies
def _set_session_cookies(response: Response, raw_secret: str, csrf: str) -> None:
    common = {
        "max_age": settings.SESSION_ABSOLUTE_TIMEOUT_MINUTES * 60,
        "path": "/",
        "domain": settings.COOKIE_DOMAIN or None,
        "secure": settings.COOKIE_SECURE,
        "samesite": settings.COOKIE_SAMESITE,
    }
    response.set_cookie(settings.SESSION_COOKIE_NAME, raw_secret, httponly=True, **common)
    # Readable by the page on purpose: it is echoed back in X-CSRF-Token.
    response.set_cookie(settings.CSRF_COOKIE_NAME, csrf, httponly=False, **common)


def _clear_session_cookies(response: Response) -> None:
    for name, httponly in (
        (settings.SESSION_COOKIE_NAME, True),
        (settings.CSRF_COOKIE_NAME, False),
    ):
        response.delete_cookie(
            name,
            path="/",
            domain=settings.COOKIE_DOMAIN or None,
            secure=settings.COOKIE_SECURE,
            httponly=httponly,
            samesite=settings.COOKIE_SAMESITE,
        )


def _login_failed(db: DbSession, user: User | None, ip: str | None, reason: str) -> ApiError:
    """Record a failed attempt durably, then hand back the generic 401.

    Committed here because the request is about to raise, and an audit row
    that rolls back with the failure would record nothing.
    """
    audit.record(
        db,
        actor_id=user.id if user else None,
        action=AuditAction.LOGIN_FAILED,
        entity_type=EntityType.USER if user else None,
        entity_id=user.id if user else None,
        after={"reason": reason},
        ip_address=ip,
    )
    db.commit()
    return unauthorized(BAD_CREDENTIALS)


# ------------------------------------------------------------------- login
@router.post("/login", response_model=TokenResponse)
def login(
    payload: LoginRequest, request: Request, response: Response, db: DbSession
) -> TokenResponse:
    ip = client_ip(request)
    # A username ("navya", "Super Admin") or a full email address.
    login_name = normalise_login(payload.identifier)
    limiter_key = f"{ip or 'unknown'}:{login_name}"
    allowed, retry_after = login_limiter.check(limiter_key)
    if not allowed:
        # Identical for existing and non-existent accounts.
        raise ApiError(
            ErrorCode.RATE_LIMITED,
            "Too many sign-in attempts. Try again later.",
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            details={"retry_after_seconds": retry_after},
        )

    lookup = (
        func.lower(User.email) == login_name
        if "@" in login_name
        else User.username == login_name
    )
    user = db.execute(select(User).where(lookup)).scalar_one_or_none()

    # The Super Admin's username and password live in the environment. If they
    # were typed exactly, make the account match before checking - so a fresh
    # database, a missed startup sync or an earlier lockout never keeps the
    # Super Admin out.
    if super_admin_env.matches_env(login_name, payload.password):
        if super_admin_env.sync(db) in ("created", "updated"):
            db.commit()
        user = db.execute(select(User).where(lookup)).scalar_one_or_none()

    if user is None:
        burn_password_check(payload.password)
        raise _login_failed(db, None, ip, "bad_credentials")

    now = utcnow()
    if user.locked_until is not None and as_naive_utc(user.locked_until) > now:
        # Locked: not even the right password gets in, and the answer is the
        # same one anybody else would get.
        burn_password_check(payload.password)
        raise _login_failed(db, user, ip, "locked")

    if not verify_password(payload.password, user.hashed_password):
        user.failed_login_count = (user.failed_login_count or 0) + 1
        if user.failed_login_count >= settings.LOGIN_LOCKOUT_THRESHOLD:
            user.locked_until = now + timedelta(minutes=settings.LOGIN_LOCKOUT_MINUTES)
            # The count starts again once the lock lapses, rather than the
            # very next typo re-locking the account.
            user.failed_login_count = 0
            audit.record(
                db,
                actor_id=None,
                action=AuditAction.LOGIN_LOCKED,
                entity_type=EntityType.USER,
                entity_id=user.id,
                after={
                    "locked_minutes": settings.LOGIN_LOCKOUT_MINUTES,
                    "threshold": settings.LOGIN_LOCKOUT_THRESHOLD,
                },
                ip_address=ip,
            )
        raise _login_failed(db, user, ip, "bad_credentials")

    if not user.is_active:
        raise _login_failed(db, user, ip, "inactive")

    user.failed_login_count = 0
    user.locked_until = None
    user.last_login_at = now
    user.last_login_ip = ip
    # The password was just proven; keep the Super Admin's readable copy of it
    # current (repairs copies made under a different key).
    password_vault.refresh_copy(user, payload.password)
    row, raw_secret = session_store.create_session(
        db, user, ip_address=ip, user_agent=request.headers.get("User-Agent")
    )
    audit.record(
        db,
        actor_id=user.id,
        action=AuditAction.LOGIN_SUCCEEDED,
        entity_type=EntityType.USER,
        entity_id=user.id,
        ip_address=ip,
    )
    db.commit()
    login_limiter.reset(limiter_key)

    csrf = csrf_token_for(row.id)
    _set_session_cookies(response, raw_secret, csrf)
    return TokenResponse(
        must_change_password=user.must_change_password,
        user=UserOut.model_validate(user),
        csrf_token=csrf,
    )


# ------------------------------------------------------------------ logout
@router.post("/logout", response_model=Message)
def logout(request: Request, response: Response, db: DbSession) -> Message:
    """End the current session. Idempotent: signing out twice, or with an
    already-expired session, still succeeds and still clears the cookies."""
    try:
        raw, via_cookie = session_credentials(request)
    except ApiError:
        raw, via_cookie = None, False

    row = session_store.find_active_session(db, raw) if raw else None
    if row is not None:
        if via_cookie and not csrf_is_valid(request, row):
            raise csrf_failed()
        session_store.revoke_session(db, row, session_store.RevokeReason.LOGOUT)
        audit.record(
            db,
            actor_id=row.user_id,
            action=AuditAction.LOGOUT,
            entity_type=EntityType.USER,
            entity_id=row.user_id,
            ip_address=client_ip(request),
        )
        db.commit()

    _clear_session_cookies(response)
    return Message(message="Signed out.")


@router.get("/me", response_model=UserOut)
def me(user: AuthenticatedUser) -> UserOut:
    """Reachable while must_change_password is set, so the UI can greet you."""
    return UserOut.model_validate(user)


@router.post("/change-password", response_model=Message)
def change_password(
    payload: ChangePasswordRequest,
    request: Request,
    response: Response,
    user: AuthenticatedUser,
    db: DbSession,
) -> Message:
    if not verify_password(payload.old_password, user.hashed_password):
        raise unauthorized("Your current password is incorrect.")
    if payload.confirm_password is not None and payload.confirm_password != payload.new_password:
        raise invalid("The new passwords do not match.")
    if payload.old_password == payload.new_password:
        raise invalid("Choose a password you have not used here before.")
    try:
        validate_password(payload.new_password, email=user.email, name=user.name)
    except PasswordPolicyError as error:
        raise invalid(str(error)) from None

    user_service.change_own_password(
        db, user, payload.new_password, ip_address=client_ip(request)
    )
    # Every session - this one included - ends, so a stolen session does not
    # outlive the password change meant to lock its thief out.
    session_store.revoke_user_sessions(
        db, user.id, session_store.RevokeReason.PASSWORD_CHANGED
    )
    db.commit()
    _clear_session_cookies(response)
    return Message(message="Password updated. Please sign in again.")
