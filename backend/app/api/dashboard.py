"""The dashboard endpoint. One route, three shapes."""
from __future__ import annotations

from fastapi import APIRouter

from app.core.deps import CurrentUser, DbSession
from app.schemas.dashboard import DashboardOut
from app.services import dashboard as dashboard_service

router = APIRouter(tags=["dashboard"])


@router.get("/dashboard", response_model=DashboardOut)
def get_dashboard(actor: CurrentUser, db: DbSession) -> DashboardOut:
    return DashboardOut(**dashboard_service.build(db, actor))
