"""FastAPI dependencies: who is calling, and what may they see."""
from __future__ import annotations

import ipaddress
import uuid
from collections.abc import Callable
from functools import lru_cache
from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy.orm import Session

from app.core import authority
from app.core import sessions as session_store
from app.core.config import settings
from app.core.constants import ADMIN_ROLES, LEADERSHIP_ROLES, ErrorCode, Role
from app.core.errors import ApiError, forbidden, unauthorized
from app.core.security import constant_time_equals, csrf_token_for
from app.db.base import as_naive_utc
from app.db.session import get_db
from app.models.org import User, UserSession

DbSession = Annotated[Session, Depends(get_db)]

#: Methods that change state and therefore need a CSRF token when the caller
#: is authenticated by cookie.
UNSAFE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})
CSRF_HEADER = "X-CSRF-Token"
CSRF_FAILED = "CSRF_FAILED"

_SESSION_EXPIRED = "Your session has expired. Please sign in again."


def session_credentials(request: Request) -> tuple[str | None, bool]:
    """(raw session secret, came_from_cookie).

    An explicit `Authorization: Bearer` header wins over the cookie: API
    clients use it, and a request carrying it cannot have been forged by
    another site (a browser never attaches it on its own), so it needs no
    CSRF token.
    """
    header = request.headers.get("Authorization", "")
    if header:
        scheme, _, token = header.partition(" ")
        token = token.strip()
        if scheme.lower() != "bearer" or not token:
            raise unauthorized("Provide a valid session.")
        return token, False
    cookie = request.cookies.get(settings.SESSION_COOKIE_NAME)
    if cookie:
        return cookie, True
    return None, False


def csrf_is_valid(request: Request, row: UserSession) -> bool:
    """Double-submit, bound to the session: the header must equal the cookie,
    and both must equal the value derived from this session's id."""
    header = request.headers.get(CSRF_HEADER)
    cookie = request.cookies.get(settings.CSRF_COOKIE_NAME)
    expected = csrf_token_for(row.id)
    return constant_time_equals(header, cookie) and constant_time_equals(header, expected)


def csrf_failed() -> ApiError:
    return forbidden(
        "Your request could not be verified. Refresh the page and try again.",
        CSRF_FAILED,
    )


def get_current_session(request: Request, db: DbSession) -> UserSession:
    """The live session behind this request, or 401 (403 on a CSRF failure)."""
    raw, via_cookie = session_credentials(request)
    if not raw:
        raise unauthorized("Please sign in.")

    row = session_store.find_active_session(db, raw)
    if row is None:
        raise unauthorized(_SESSION_EXPIRED)

    user = db.get(User, row.user_id)
    if user is None:
        raise unauthorized(_SESSION_EXPIRED)
    if not user.is_active:
        raise unauthorized("This account has been deactivated.")
    # Belt and braces: every password change/reset also revokes sessions, but
    # a session that predates the current password is dead regardless of
    # whether the code path that changed it remembered to.
    if user.password_changed_at is not None and as_naive_utc(row.created_at) < as_naive_utc(
        user.password_changed_at
    ):
        raise unauthorized("Your password changed. Please sign in again.")

    if via_cookie and request.method.upper() in UNSAFE_METHODS:
        if not csrf_is_valid(request, row):
            raise csrf_failed()

    if session_store.touch(db, row):
        # Nothing else is pending this early in the request; persisting the
        # sliding idle window must not depend on the route committing.
        db.commit()

    request.state.session = row
    request.state.user_id = row.user_id
    # Kept so get_current_user reuses this object: the session's identity map
    # holds users weakly, and a second lookup would otherwise hit the database.
    request.state.user = user
    return row


CurrentSession = Annotated[UserSession, Depends(get_current_session)]


def get_current_user(request: Request, db: DbSession, row: CurrentSession) -> User:
    """The authenticated caller, or 401.

    Deliberately does NOT enforce must_change_password - /auth/me and
    /auth/change-password have to stay reachable while it is set. Every other
    router depends on `CurrentUser` below, which does enforce it.
    """
    user = getattr(request.state, "user", None)
    if user is None or user.id != row.user_id:
        user = db.get(User, row.user_id)
    assert user is not None
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


_Network = ipaddress.IPv4Network | ipaddress.IPv6Network


@lru_cache(maxsize=8)
def _trusted_networks(raw: str) -> tuple[_Network, ...]:
    networks: list[_Network] = []
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        try:
            networks.append(ipaddress.ip_network(part, strict=False))
        except ValueError:
            continue  # a typo in config must not trust everything
    return tuple(networks)


def _parse_ip(value: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address | None:
    try:
        return ipaddress.ip_address(value.strip())
    except ValueError:
        return None


def _is_trusted(ip: ipaddress.IPv4Address | ipaddress.IPv6Address, networks) -> bool:
    return any(ip.version == net.version and ip in net for net in networks)


def client_ip(request: Request) -> str | None:
    """The caller's IP, for rate limiting and the audit trail.

    X-Forwarded-For is attacker-controlled unless it was written by our own
    reverse proxy, so it is honoured ONLY when the socket peer is listed in
    TRUSTED_PROXIES. The chain is walked from the right, skipping trusted
    hops; the first untrusted address is the client.
    """
    peer = request.client.host if request.client else None
    networks = _trusted_networks(settings.TRUSTED_PROXIES or "")
    peer_ip = _parse_ip(peer) if peer else None
    if not networks or peer_ip is None or not _is_trusted(peer_ip, networks):
        return peer

    forwarded = request.headers.get("X-Forwarded-For", "")
    hops = [hop.strip() for hop in forwarded.split(",") if hop.strip()]
    candidate = peer
    for hop in reversed(hops):
        hop_ip = _parse_ip(hop)
        if hop_ip is None:
            break  # garbage in the chain: stop at the last good address
        candidate = str(hop_ip)
        if not _is_trusted(hop_ip, networks):
            break
    return candidate
