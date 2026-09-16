"""Runtime settings and the audit trail.

Both were specified in plan v3 s9.1 and had no read API until now: the audit
rows were being written and never surfaced, which makes them a log nobody can
consult.
"""
from __future__ import annotations

import tempfile
import uuid
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from fastapi import APIRouter, File, Query, Request, UploadFile
from pydantic import BaseModel
from sqlalchemy import func, select

from app.core.config import settings
from app.core.constants import AuditAction
from app.core.deps import AdminUser, DbSession, SuperAdminUser, client_ip
from app.core.errors import invalid
from app.models.org import User
from app.models.system import AuditEvent
from app.schemas.common import ORMModel, Page
from app.services import audit, runtime_settings, sap_sync
from app.services.customer_source import UnmappedColumns
from app.services.sap_import import ImportResult

router = APIRouter(prefix="/admin", tags=["admin"])


class SettingsOut(BaseModel):
    values: dict[str, Any]


class SettingsUpdate(BaseModel):
    values: dict[str, Any]


class AuditEventOut(ORMModel):
    id: uuid.UUID
    actor_user_id: uuid.UUID | None = None
    actor_name: str | None = None
    action: str
    entity_type: str | None = None
    entity_id: uuid.UUID | None = None
    before: dict | None = None
    after: dict | None = None
    ip_address: str | None = None
    created_at: datetime


@router.get("/settings", response_model=SettingsOut)
def get_settings(_: AdminUser, db: DbSession) -> SettingsOut:
    return SettingsOut(values=runtime_settings.all_settings(db))


@router.patch("/settings", response_model=SettingsOut)
def update_settings(
    payload: SettingsUpdate, request: Request, actor: AdminUser, db: DbSession
) -> SettingsOut:
    unknown = [key for key in payload.values if key not in runtime_settings.DEFAULTS]
    if unknown:
        raise invalid(
            "Unknown setting(s): " + ", ".join(sorted(unknown)),
            known_keys=sorted(runtime_settings.DEFAULTS),
        )

    before = runtime_settings.all_settings(db)
    for key, value in payload.values.items():
        runtime_settings.set_value(db, key, value, actor_id=actor.id)
    after = runtime_settings.all_settings(db)

    changed_before, changed_after = audit.diff(before, after)
    if changed_after:
        audit.record(
            db,
            actor_id=actor.id,
            action=AuditAction.SETTINGS_UPDATED,
            entity_type="SETTING",
            before=changed_before,
            after=changed_after,
            ip_address=client_ip(request),
        )
    db.commit()
    return SettingsOut(values=after)


@router.get("/sap-sync/status")
def sap_sync_status(_: SuperAdminUser) -> dict[str, Any]:
    """Super Admin only - is the SAP workbook linked, and when did it last sync?"""
    return sap_sync.status()


@router.post("/sap-import")
def run_sap_import(actor: SuperAdminUser, db: DbSession) -> dict[str, Any]:
    """Super Admin only - import the linked SAP workbook (SAP_DATA_FILE) now.

    The same import the background sync runs; see services/sap_import.py for
    exactly what it changes.
    """
    if sap_sync.linked_file() is not None:
        return _guarded(lambda: [sap_sync.sync_linked_file(db, actor=actor)])

    files = settings.sap_data_files
    if not files:
        raise invalid(f"No SAP data file found in {settings.sap_data_dir}")
    return _guarded(lambda: [sap_sync.run_import(db, path, actor=actor) for path in files])


#: Same ceiling as the feedback upload.
MAX_SAP_UPLOAD_BYTES = 10 * 1024 * 1024


@router.post("/sap-import/upload")
def upload_sap_import(
    actor: SuperAdminUser, db: DbSession, file: UploadFile = File(...)
) -> dict[str, Any]:
    """Super Admin only - import customers from a SAP file chosen on the computer."""
    filename = Path(file.filename or "").name
    if not filename.lower().endswith((".xlsx", ".xlsm", ".xls", ".csv")):
        raise invalid("Choose an Excel (.xlsx / .xlsm / .xls) or .csv file.")
    content = file.file.read(MAX_SAP_UPLOAD_BYTES + 1)
    if len(content) > MAX_SAP_UPLOAD_BYTES:
        raise invalid("That file is larger than 10MB.")

    with tempfile.TemporaryDirectory() as scratch:
        path = Path(scratch) / filename
        path.write_bytes(content)
        response = _guarded(lambda: [sap_sync.run_import(db, path, actor=actor)])
    # The linked workbook stays the source of truth: the next sync re-applies it.
    sap_sync.forget_linked_signature()
    return response


def _guarded(run: Callable[[], list[ImportResult]]) -> dict[str, Any]:
    """Run imports, turning an unreadable file into a clear 422."""
    try:
        results = run()
    except sap_sync.SyncBusy as exc:
        raise invalid(str(exc)) from exc
    except FileNotFoundError as exc:
        raise invalid(str(exc)) from exc
    except UnmappedColumns as exc:
        raise invalid(str(exc)) from exc
    except PermissionError as exc:
        raise invalid("Could not open the file. Close it in Excel and try again.") from exc
    except (ValueError, KeyError, zipfile.BadZipFile) as exc:
        raise invalid(f"Could not read the file: {exc}") from exc
    return {
        "files": [result.as_dict() for result in results],
        "mobiles_dropped": sum(result.mobiles_dropped for result in results),
    }


@router.get("/audit", response_model=Page[AuditEventOut])
def list_audit(
    _: SuperAdminUser,
    db: DbSession,
    action: str | None = Query(default=None),
    entity_type: str | None = Query(default=None),
    actor_user_id: uuid.UUID | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
) -> Page[AuditEventOut]:
    """Super Admin only — the mutation log (plan v3 s9.1)."""
    stmt = select(AuditEvent)
    if action:
        stmt = stmt.where(AuditEvent.action == action)
    if entity_type:
        stmt = stmt.where(AuditEvent.entity_type == entity_type)
    if actor_user_id is not None:
        stmt = stmt.where(AuditEvent.actor_user_id == actor_user_id)

    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = (
        db.execute(
            stmt.order_by(AuditEvent.created_at.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        .scalars()
        .all()
    )

    names = dict(
        db.execute(
            select(User.id, User.name).where(
                User.id.in_({row.actor_user_id for row in rows if row.actor_user_id})
            )
        ).all()
    ) if rows else {}

    items = []
    for row in rows:
        item = AuditEventOut.model_validate(row)
        item.actor_name = names.get(row.actor_user_id)
        items.append(item)

    return Page[AuditEventOut](items=items, total=total, page=page, page_size=page_size)
