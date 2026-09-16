"""Sign in, sign out and the forced password change."""
from __future__ import annotations

from fastapi import APIRouter, Request, status
from sqlalchemy import func, select

from app.core.config import settings
from app.core.constants import ErrorCode
from app.core.deps import AuthenticatedUser, DbSession, client_ip
from app.core.errors import ApiError, unauthorized
from app.core.ratelimit import SlidingWindowLimiter
from app.core.security import create_access_token, verify_password
from app.models.org import User
from app.schemas.common import Message
from app.schemas.user import (
    ChangePasswordRequest,
    LoginRequest,
    TokenResponse,
    UserOut,
)
from app.services import users as user_service

router = APIRouter(prefix="/auth", tags=["auth"])

login_limiter = SlidingWindowLimiter(
    settings.LOGIN_RATE_LIMIT_ATTEMPTS, settings.LOGIN_RATE_LIMIT_WINDOW_SECONDS
)


@router.post("/login", response_model=TokenResponse)
def login(payload: LoginRequest, request: Request, db: DbSession) -> TokenResponse:
    ip = client_ip(request) or "unknown"
    allowed, retry_after = login_limiter.check(f"{ip}:{payload.email.lower()}")
    if not allowed:
        raise ApiError(
            ErrorCode.RATE_LIMITED,
            f"Too many sign-in attempts. Try again in {retry_after} seconds.",
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            details={"retry_after_seconds": retry_after},
        )

    user = db.execute(
        select(User).where(func.lower(User.email) == payload.email.strip().lower())
    ).scalar_one_or_none()

    # One message for "no such account", "wrong password" and "deactivated",
    # so the endpoint cannot be used to enumerate staff.
    if (
        user is None
        or not user.is_active
        or not verify_password(payload.password, user.hashed_password)
    ):
        raise unauthorized("Incorrect email or password.")

    login_limiter.reset(f"{ip}:{payload.email.lower()}")
    return TokenResponse(
        access_token=create_access_token(
            user.id, password_changed_at=user.password_changed_at
        ),
        must_change_password=user.must_change_password,
        user=UserOut.model_validate(user),
    )


@router.get("/me", response_model=UserOut)
def me(user: AuthenticatedUser) -> UserOut:
    """Reachable while must_change_password is set, so the UI can greet you."""
    return UserOut.model_validate(user)


@router.post("/change-password", response_model=Message)
def change_password(
    payload: ChangePasswordRequest,
    request: Request,
    user: AuthenticatedUser,
    db: DbSession,
) -> Message:
    if not verify_password(payload.old_password, user.hashed_password):
        raise unauthorized("Your current password is incorrect.")
    if payload.old_password == payload.new_password:
        raise ApiError(
            ErrorCode.VALIDATION_ERROR,
            "Choose a password you have not used here before.",
            status_code=422,
        )

    user_service.change_own_password(
        db, user, payload.new_password, ip_address=client_ip(request)
    )
    db.commit()
    # The caller's current token was issued before password_changed_at, so it
    # is now invalid - the UI signs them in again with the new password.
    return Message(message="Password updated. Please sign in again.")
