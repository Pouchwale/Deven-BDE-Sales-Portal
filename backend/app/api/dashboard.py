"""The dashboard endpoint. One route, three shapes."""
from __future__ import annotations

from fastapi import APIRouter

from app.core.constants import ErrorCode
from app.core.deps import CurrentUser, DbSession
from app.core.errors import ApiError
from app.core.ratelimit import SlidingWindowLimiter
from app.models.org import User
from app.schemas.dashboard import DashboardOut
from app.services import dashboard as dashboard_service

router = APIRouter(tags=["dashboard"])

#: The dashboard and the feedback analysis are the two screens built from
#: many aggregate queries at once. Nothing polls either of them, so a person
#: clicking around makes a handful of calls a minute; this stops a script (or
#: a stuck refresh loop) from turning them into a load generator.
ANALYTICS_RATE_LIMIT = 60
ANALYTICS_RATE_WINDOW_SECONDS = 60
analytics_limiter = SlidingWindowLimiter(ANALYTICS_RATE_LIMIT, ANALYTICS_RATE_WINDOW_SECONDS)


def check_analytics_rate(actor: User, bucket: str) -> None:
    allowed, retry_after = analytics_limiter.check(f"{bucket}:{actor.id}")
    if not allowed:
        raise ApiError(
            ErrorCode.RATE_LIMITED,
            "Too many refreshes in a short time. Wait a moment and try again.",
            status_code=429,
            details={"retry_after_seconds": retry_after},
        )


@router.get("/dashboard", response_model=DashboardOut)
def get_dashboard(actor: CurrentUser, db: DbSession) -> DashboardOut:
    check_analytics_rate(actor, "dashboard")
    return DashboardOut(**dashboard_service.build(db, actor))
