"""FastAPI dependencies: who is calling, and what may they see."""
from __future__ import annotations

import uuid
from collections.abc import Callable
from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy.orm import Session

from app.core import authority
from app.core.constants import ADMIN_ROLES, LEADERSHIP_ROLES, ErrorCode, Role
from app.core.errors import forbidden, unauthorized
from app.core.security import decode_access_token, token_is_stale
from app.db.session import get_db
from app.models.org import User

DbSession = Annotated[Session, Depends(get_db)]


def _bearer_token(request: Request) -> str:
    header = request.headers.get("Authorization", "")
    scheme, _, token = header.partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise unauthorized("Provide a bearer token.")
    return token


def get_current_user(request: Request, db: DbSession) -> User:
    """The authenticated caller, or 401.

    Deliberately does NOT enforce must_change_password - /auth/me and
    /auth/change-password have to stay reachable while it is set. Every other
    router depends on `CurrentUser` below, which does enforce it.
    """
    claims = decode_access_token(_bearer_token(request))
    if not claims or not claims.get("sub"):
        raise unauthorized("Your session has expired. Please sign in again.")

    try:
        user_id = uuid.UUID(str(claims["sub"]))
    except ValueError:
        raise unauthorized("Malformed token.") from None

    user = db.get(User, user_id)
    if user is None:
        raise unauthorized("Your account no longer exists.")
    if not user.is_active:
        raise unauthorized("This account has been deactivated.")
    if token_is_stale(claims, user.password_changed_at):
        raise unauthorized("Your password changed. Please sign in again.")
    return user


AuthenticatedUser = Annotated[User, Depends(get_current_user)]


def require_password_current(user: AuthenticatedUser) -> User:
    """Blocks the rest of the API until a forced password change is done."""
    if user.must_change_password:
        raise forbidden(
            "Set a new password before continuing.",
            ErrorCode.PASSWORD_CHANGE_REQUIRED,
        )
    return user


CurrentUser = Annotated[User, Depends(require_password_current)]


def require_roles(*roles: str) -> Callable[..., User]:
    """Route guard: the caller must hold one of these roles."""
    allowed = {str(r) for r in roles}

    def dependency(user: CurrentUser) -> User:
        if user.role not in allowed:
            raise forbidden(
                "You do not have permission to use this feature.",
                ErrorCode.FORBIDDEN,
                required_roles=sorted(allowed),
            )
        return user

    return dependency


# The three guards actually used, named so a route reads as its own spec.
require_admin = require_roles(*ADMIN_ROLES)
require_leadership = require_roles(*LEADERSHIP_ROLES)
require_super_admin = require_roles(Role.SUPER_ADMIN)

AdminUser = Annotated[User, Depends(require_admin)]
LeadershipUser = Annotated[User, Depends(require_leadership)]
SuperAdminUser = Annotated[User, Depends(require_super_admin)]


# A concrete type, never a string forward reference: FastAPI resolves the
# annotation at import time, and an unresolvable one is silently treated as a
# query parameter rather than a dependency - which turns every scoped endpoint
# into a 422 asking for `?scope=`.
ScopeType = set[uuid.UUID] | authority._All


def get_visibility_scope(user: CurrentUser, db: DbSession) -> ScopeType:
    """The set of user ids whose rows the caller may see, or ALL.

    Every list endpoint takes this and turns it into a WHERE clause. Applying
    it as a dependency rather than by hand in each route is what stops one
    forgotten filter leaking a whole team's pipeline.
    """
    return authority.visible_user_ids(db, user)


VisibilityScope = Annotated[ScopeType, Depends(get_visibility_scope)]


def client_ip(request: Request) -> str | None:
    """Best-effort caller IP for the audit trail."""
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else None
