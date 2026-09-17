"""Runtime settings and the audit trail.

Both were specified in plan v3 s9.1 and had no read API until now: the audit
rows were being written and never surfaced, which makes them a log nobody can
consult.
"""
from __future__ import annotations

import os
import tempfile
import uuid
import zipfile
from contextlib import suppress
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from fastapi import APIRouter, File, Query, Request, UploadFile
from pydantic import BaseModel
from sqlalchemy import func, select

from app.core.config import settings
from app.core.constants import AuditAction, EntityType, ErrorCode
from app.core.deps import AdminUser, DbSession, SuperAdminUser, client_ip
from app.core.errors import ApiError, invalid
from app.core.logging import get_logger
from app.core.ratelimit import SlidingWindowLimiter
from app.models.org import User
from app.models.system import AuditEvent
from app.schemas.common import MAX_PAGE, MAX_PAGE_SIZE, ORMModel, Page
from app.services import audit, customer_source, runtime_settings, sap_sync
from app.services.customer_source import UnmappedColumns
from app.services.sap_import import ImportResult

log = get_logger(__name__)

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

    for key, value in payload.values.items():
        try:
            runtime_settings.validate_value(key, value)
        except runtime_settings.InvalidSetting as exc:
            raise invalid(str(exc), key=key) from None

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
    """Super Admin only - is the SAP workbook linked, and when did it last sync?

    Carries the linked file's NAME only, never its server path.
    """
    return sap_sync.status()


#: Imports are expensive (whole-file parse + one long transaction). Shared by
#: the Sync button and the upload, per user, per process.
SAP_IMPORT_RATE_LIMIT = 6
SAP_IMPORT_RATE_WINDOW_SECONDS = 300
sap_import_limiter = SlidingWindowLimiter(
    SAP_IMPORT_RATE_LIMIT, SAP_IMPORT_RATE_WINDOW_SECONDS
)


def _check_import_rate(actor: User) -> None:
    allowed, retry_after = sap_import_limiter.check(str(actor.id))
    if not allowed:
        raise ApiError(
            ErrorCode.RATE_LIMITED,
            "Too many SAP imports in a short time. Try again in a few minutes.",
            status_code=429,
            details={"retry_after_seconds": retry_after},
        )


@router.post("/sap-import")
def run_sap_import(
    request: Request, actor: SuperAdminUser, db: DbSession
) -> dict[str, Any]:
    """Super Admin only - import the linked SAP workbook (SAP_DATA_FILE) now.

    Takes no input: it only ever imports the file the SERVER is configured
    with. The same import the background sync runs; see services/sap_import.py
    for exactly what it changes.
    """
    _check_import_rate(actor)
    ip = client_ip(request)
    linked = sap_sync.linked_file()
    if linked is not None:
        return _guarded(
            db,
            actor,
            ip,
            linked.name,
            lambda: [sap_sync.sync_linked_file(db, actor=actor, ip_address=ip)],
        )

    files = settings.sap_data_files
    if not files:
        raise invalid("No SAP data file is configured on the server.")
    return _guarded(
        db,
        actor,
        ip,
        ", ".join(path.name for path in files),
        lambda: [sap_sync.run_import(db, path, actor=actor, ip_address=ip) for path in files],
    )


#: Same ceiling as the feedback upload.
MAX_SAP_UPLOAD_BYTES = customer_source.MAX_FILE_BYTES
_UPLOAD_CHUNK_BYTES = 1024 * 1024


@router.post("/sap-import/upload")
def upload_sap_import(
    request: Request,
    actor: SuperAdminUser,
    db: DbSession,
    file: UploadFile = File(...),
) -> dict[str, Any]:
    """Super Admin only - import customers from a SAP file chosen on the computer.

    The client's filename is used for display and the audit trail only (as a
    sanitised basename). The bytes go to a private temp file with a random
    name, are checked by content (see customer_source.check_file), and the
    temp file is always deleted.
    """
    _check_import_rate(actor)
    ip = client_ip(request)
    display_name = customer_source.safe_display_name(file.filename)
    suffix = Path(display_name).suffix.lower()
    if suffix not in customer_source.ALLOWED_SUFFIXES:
        raise invalid("Choose an Excel (.xlsx / .xlsm) or .csv file.")

    handle, temp_name = tempfile.mkstemp(
        prefix=customer_source.TEMP_PREFIX + "upload_", suffix=suffix
    )
    temp_path = Path(temp_name)
    try:
        written = 0
        with os.fdopen(handle, "wb") as out:
            while chunk := file.file.read(_UPLOAD_CHUNK_BYTES):
                written += len(chunk)
                if written > MAX_SAP_UPLOAD_BYTES:
                    raise ApiError(
                        ErrorCode.VALIDATION_ERROR,
                        f"That file is larger than {MAX_SAP_UPLOAD_BYTES // (1024 * 1024)}MB.",
                        status_code=413,
                    )
                out.write(chunk)
        response = _guarded(
            db,
            actor,
            ip,
            display_name,
            lambda: [
                sap_sync.run_import(
                    db, temp_path, actor=actor, display_name=display_name, ip_address=ip
                )
            ],
        )
    finally:
        with suppress(OSError):
            temp_path.unlink()
    # The linked workbook stays the source of truth: the next sync re-applies it.
    sap_sync.forget_linked_signature()
    return response


def _guarded(
    db: DbSession,
    actor: User,
    ip: str | None,
    filename: str,
    run: Callable[[], list[ImportResult]],
) -> dict[str, Any]:
    """Run imports, turning an unreadable file into a clear, path-free 422.

    A refused or failed import has been rolled back, so its SAP_IMPORTED audit
    row went with it; a separate FAILED row is written so the attempt is still
    on the record.
    """
    try:
        results = run()
    except sap_sync.SyncBusy as exc:
        raise invalid(str(exc)) from exc
    except customer_source.SourceFileRejected as exc:
        _audit_failure(db, actor, ip, filename, str(exc))
        raise ApiError(
            ErrorCode.VALIDATION_ERROR, str(exc), status_code=exc.status_code
        ) from None
    except UnmappedColumns as exc:
        _audit_failure(db, actor, ip, filename, str(exc))
        raise invalid(
            "The file is missing required SAP columns: " + ", ".join(exc.captions),
            ErrorCode.UNMAPPED_COLUMNS,
            missing_columns=exc.captions,
        ) from None
    except FileNotFoundError as exc:
        message = sap_sync.safe_error(exc, sap_sync.linked_file())
        _audit_failure(db, actor, ip, filename, message)
        raise invalid(message) from None
    except PermissionError:
        _audit_failure(db, actor, ip, filename, "file locked")
        raise invalid("Could not open the file. Close it in Excel and try again.") from None
    except (ValueError, KeyError, zipfile.BadZipFile) as exc:
        log.warning("SAP import failed", extra={"sap_file": filename, "error_type": type(exc).__name__})
        _audit_failure(db, actor, ip, filename, type(exc).__name__)
        raise invalid("Could not read the file. Check it is the SAP export and try again.") from None
    return {
        "files": [result.as_dict() for result in results],
        "mobiles_dropped": sum(result.mobiles_dropped for result in results),
    }


def _audit_failure(
    db: DbSession, actor: User, ip: str | None, filename: str, reason: str
) -> None:
    try:
        db.rollback()
        audit.record(
            db,
            actor_id=actor.id,
            action=AuditAction.SAP_IMPORTED,
            entity_type=EntityType.IMPORT,
            after={"filename": filename, "result": "REJECTED", "reason": reason[:300]},
            ip_address=ip,
        )
        db.commit()
    except Exception:  # noqa: BLE001 - never mask the user's real error
        db.rollback()
        log.exception("Could not audit a failed SAP import")


#: Audit filter values are free text in the database; bounded, never interpolated.
_AUDIT_FILTER = r"^[A-Z0-9_]{1,60}$"


@router.get("/audit", response_model=Page[AuditEventOut])
def list_audit(
    _: SuperAdminUser,
    db: DbSession,
    action: str | None = Query(default=None, pattern=_AUDIT_FILTER),
    entity_type: str | None = Query(default=None, pattern=_AUDIT_FILTER),
    actor_user_id: uuid.UUID | None = Query(default=None),
    page: int = Query(default=1, ge=1, le=MAX_PAGE),
    page_size: int = Query(default=50, ge=1, le=MAX_PAGE_SIZE),
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
