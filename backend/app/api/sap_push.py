"""The SAP workbook, pushed from the office PC.

The workbook is refreshed from SAP on one Windows PC every morning (Excel has
the SAP connection; the hosted portal cannot reach SAP or that PC's files).
`deploy/sync/sap_uploader.py` on that PC sends the saved workbook here, and
this route runs the very same import as the Super Admin "Upload SAP file"
button - which is idempotent, so a repeated push creates nothing new.

No user signs in for this. The request is signed with SAP_PUSH_SECRET, which
only the PC (encrypted under the Windows account, via DPAPI) and the server
(an environment variable) hold - the same scheme as the Google Form webhook:

    X-Portal-Signature: t=<unix seconds>,v1=<hex HMAC-SHA256(secret, "<t>." + body)>

Order of checks: configured, size, signature (and freshness), rate - so
unauthenticated noise is refused before anything is parsed or stored, and
nothing says WHICH signature check failed.
"""
from __future__ import annotations

import hashlib
import hmac
import os
import tempfile
import time
import zipfile
from contextlib import suppress
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Request
from starlette.concurrency import run_in_threadpool

from app.core.config import settings
from app.core.constants import AuditAction, EntityType, ErrorCode
from app.core.deps import DbSession, client_ip
from app.core.errors import ApiError
from app.core.logging import get_logger
from app.core.ratelimit import SlidingWindowLimiter
from app.services import audit, customer_source, sap_sync
from app.services.customer_source import UnmappedColumns

log = get_logger(__name__)

router = APIRouter(prefix="/admin/sap-import", tags=["admin"])

SIGNATURE_HEADER = "X-Portal-Signature"
FILENAME_HEADER = "X-Sap-Filename"

#: The uploader checks every 15 minutes and only sends a changed file, so a
#: dozen an hour is far above honest use and well below anything abusive.
push_limiter = SlidingWindowLimiter(max_attempts=12, window_seconds=3600)


def sign(body: bytes, secret: str, timestamp: int | None = None) -> str:
    """The header the uploader sends. Used by the uploader's twin and tests."""
    stamp = int(time.time()) if timestamp is None else timestamp
    digest = hmac.new(secret.encode(), f"{stamp}.".encode() + body, hashlib.sha256).hexdigest()
    return f"t={stamp},v1={digest}"


def _unauthorized() -> ApiError:
    # One answer for every signature failure: the route is not an oracle.
    return ApiError(ErrorCode.UNAUTHORIZED, "Not authenticated.", status_code=401)


def _verify(body: bytes, header: str | None) -> None:
    if not header:
        raise _unauthorized()
    parts = dict(piece.split("=", 1) for piece in header.split(",") if "=" in piece)
    stamp, provided = parts.get("t"), parts.get("v1")
    if not stamp or not provided:
        raise _unauthorized()
    try:
        sent_at = int(stamp)
    except ValueError:
        raise _unauthorized() from None
    # A captured request stops working after the window.
    if abs(int(time.time()) - sent_at) > settings.SAP_PUSH_MAX_SKEW_SECONDS:
        raise _unauthorized()
    expected = hmac.new(
        settings.SAP_PUSH_SECRET.strip().encode(), f"{sent_at}.".encode() + body, hashlib.sha256
    ).hexdigest()
    if not hmac.compare_digest(expected, provided):
        raise _unauthorized()


def _audit_failure(db: DbSession, ip: str | None, filename: str, reason: str) -> None:
    try:
        db.rollback()
        audit.record(
            db,
            actor_id=None,
            action=AuditAction.SAP_IMPORTED,
            entity_type=EntityType.IMPORT,
            after={"filename": filename, "result": "REJECTED", "reason": reason[:300], "via": "push"},
            ip_address=ip,
        )
        db.commit()
    except Exception:  # noqa: BLE001 - never mask the real error
        db.rollback()
        log.exception("Could not audit a failed SAP push")


@router.post("/push")
async def push_sap_workbook(request: Request, db: DbSession) -> dict[str, Any]:
    """Import a SAP workbook pushed by the office PC's uploader."""
    if not settings.sap_push_enabled:
        raise ApiError("NOT_CONFIGURED", "SAP push is not enabled on this server.", status_code=503)

    limit = customer_source.MAX_FILE_BYTES
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > limit:
        raise ApiError(ErrorCode.VALIDATION_ERROR, "That file is too large.", status_code=413)
    body = await request.body()
    if len(body) > limit:
        raise ApiError(ErrorCode.VALIDATION_ERROR, "That file is too large.", status_code=413)

    _verify(body, request.headers.get(SIGNATURE_HEADER))

    ip = client_ip(request)
    allowed, retry_after = push_limiter.check(ip or "unknown")
    if not allowed:
        raise ApiError(
            ErrorCode.RATE_LIMITED,
            "Too many SAP pushes. Try again later.",
            status_code=429,
            details={"retry_after_seconds": retry_after},
        )

    display_name = customer_source.safe_display_name(
        request.headers.get(FILENAME_HEADER), default="sap-push.xlsm"
    )
    suffix = Path(display_name).suffix.lower()
    if suffix not in customer_source.ALLOWED_SUFFIXES:
        raise ApiError(ErrorCode.VALIDATION_ERROR, "Send an Excel (.xlsx / .xlsm) or .csv file.", status_code=422)

    handle, temp_name = tempfile.mkstemp(prefix=customer_source.TEMP_PREFIX + "push_", suffix=suffix)
    temp_path = Path(temp_name)
    try:
        with os.fdopen(handle, "wb") as out:
            out.write(body)
        try:
            # The import is synchronous and holds a lock; keep it off the
            # event loop so the rest of the portal stays responsive.
            result = await run_in_threadpool(
                sap_sync.run_import, db, temp_path, actor=None, display_name=display_name, ip_address=ip
            )
        except sap_sync.SyncBusy as exc:
            raise ApiError("SYNC_BUSY", str(exc), status_code=409) from None
        except customer_source.SourceFileRejected as exc:
            _audit_failure(db, ip, display_name, str(exc))
            raise ApiError(ErrorCode.VALIDATION_ERROR, str(exc), status_code=exc.status_code) from None
        except UnmappedColumns as exc:
            _audit_failure(db, ip, display_name, str(exc))
            raise ApiError(
                ErrorCode.UNMAPPED_COLUMNS,
                "The file is missing required SAP columns: " + ", ".join(exc.captions),
                status_code=422,
                details={"missing_columns": exc.captions},
            ) from None
        except (ValueError, KeyError, zipfile.BadZipFile) as exc:
            log.warning("SAP push failed", extra={"sap_file": display_name, "error_type": type(exc).__name__})
            _audit_failure(db, ip, display_name, type(exc).__name__)
            raise ApiError(
                ErrorCode.VALIDATION_ERROR,
                "Could not read the file. Check it is the SAP export.",
                status_code=422,
            ) from None
    finally:
        with suppress(OSError):
            temp_path.unlink()

    # A server with its own linked workbook keeps treating that as the truth.
    sap_sync.forget_linked_signature()
    return {"files": [result.as_dict()], "mobiles_dropped": result.mobiles_dropped}
