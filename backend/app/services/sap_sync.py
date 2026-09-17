"""Keeping the portal in step with the live SAP workbook (SAP_DATA_FILE).

The workbook is edited all day, so waiting for somebody to press "Import" is
how the portal ends up a day behind. A background thread checks the file every
SAP_SYNC_INTERVAL_SECONDS and imports it as soon as a new save lands.

Safety:
  * One import at a time. The watcher, the Sync button and an upload share
    one lock, so two imports can never interleave.
  * Only a SETTLED save is read: the file must have been unchanged for a few
    seconds, so a save still being written by Excel or OneDrive is not read
    half-way.
  * All or nothing. An import runs in one transaction; if the file cannot be
    read (mid-save, corrupt, wrong columns) it is rolled back, nothing changes,
    and the next check tries again.
  * What the workbook shows but has not SAVED cannot be seen by anyone - the
    status reports the file's last-saved time so that is visible.
  * The source is SERVER configuration only (SAP_DATA_FILE in the
    environment). No endpoint accepts a path, and neither the status nor an
    error message ever carries one back to a browser - only the file's name.
"""
from __future__ import annotations

import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.logging import get_logger
from app.db.session import SessionLocal
from app.models.org import User
from app.services.customer_source import FileCustomerSource, scrub_paths
from app.services.sap_import import ImportResult, import_source

log = get_logger("app.sap_sync")

#: A save must be this old before it is read.
SETTLE_SECONDS = 3

_lock = threading.Lock()
_stop = threading.Event()
_thread: threading.Thread | None = None
#: (mtime_ns, size) of the linked file as last imported successfully.
_imported_signature: tuple[int, int] | None = None
_state: dict[str, Any] = {
    "last_checked_at": None,
    "last_synced_at": None,
    "last_synced_file_saved_at": None,
    "last_result": None,
    "last_error": None,
    "last_error_at": None,
}


class SyncBusy(RuntimeError):
    """Another import is already running."""


def _now() -> datetime:
    return datetime.now(timezone.utc)


def linked_file() -> Path | None:
    value = settings.SAP_DATA_FILE.strip().strip("'\"")
    return Path(value) if value else None


def _signature(path: Path) -> tuple[int, int]:
    stat = path.stat()
    return stat.st_mtime_ns, stat.st_size


def safe_error(exc: BaseException, path: Path | None = None) -> str:
    """An exception as a sentence with no filesystem path in it."""
    message = str(exc) or type(exc).__name__
    return scrub_paths(message, path, path.parent if path else None)


def run_import(
    db: Session,
    path: Path,
    *,
    actor: User | None = None,
    display_name: str | None = None,
    ip_address: str | None = None,
) -> ImportResult:
    """Import one file under the shared lock. Commits, or rolls back and raises."""
    if not _lock.acquire(timeout=120):
        raise SyncBusy("Another SAP import is still running. Try again in a moment.")
    try:
        try:
            result = import_source(
                db,
                FileCustomerSource(path, display_name=display_name),
                actor=actor,
                ip_address=ip_address,
            )
            db.commit()
        except Exception:
            db.rollback()
            raise
        return result
    finally:
        _lock.release()


def sync_linked_file(
    db: Session, *, actor: User | None = None, ip_address: str | None = None
) -> ImportResult:
    """Import the linked workbook now and record the outcome in the status."""
    global _imported_signature
    path = linked_file()
    if path is None:
        raise FileNotFoundError("No SAP workbook is linked (SAP_DATA_FILE is blank).")
    if not path.exists():
        _record_error(f"Linked SAP workbook not found: {path.name}", path)
        raise FileNotFoundError(f"Linked SAP workbook not found: {path.name}")

    signature = _signature(path)
    try:
        result = run_import(db, path, actor=actor, ip_address=ip_address)
    except SyncBusy:
        raise
    except Exception as exc:
        _record_error(safe_error(exc, path), path, exc)
        raise
    _imported_signature = signature
    _state.update(
        last_synced_at=_now(),
        last_synced_file_saved_at=datetime.fromtimestamp(
            signature[0] / 1e9, tz=timezone.utc
        ),
        last_result=result.as_dict(),
        last_error=None,
        last_error_at=None,
    )
    return result


def forget_linked_signature() -> None:
    """Make the next check re-import the linked file even if it is unchanged.

    Called after an upload: the live workbook is the source of truth, so if an
    uploaded file said something different, the workbook's version is put back.
    """
    global _imported_signature
    _imported_signature = None


def _record_error(
    message: str, path: Path | None = None, exc: BaseException | None = None
) -> None:
    """Remember a failure for the status line, and log it once per change.

    The watcher retries every interval; logging the same failure every 30
    seconds would bury everything else, so only a NEW message is logged.
    """
    safe = scrub_paths(message, path, path.parent if path else None)[:500]
    changed = safe != _state.get("last_error")
    _state.update(last_error=safe, last_error_at=_now())
    if changed:
        log.error(
            "SAP sync failed: %s",
            safe,
            extra={
                "sap_file": path.name if path else None,
                "error_type": type(exc).__name__ if exc else None,
            },
        )


def check_once() -> bool:
    """One watcher tick. True if an import ran."""
    _state["last_checked_at"] = _now()
    path = linked_file()
    if path is None:
        return False
    try:
        if not path.exists():
            _record_error(f"Linked SAP workbook not found: {path.name}", path)
            return False
        signature = _signature(path)
    except OSError as exc:
        _record_error(
            f"Cannot read linked SAP workbook {path.name}: {safe_error(exc, path)}",
            path,
            exc,
        )
        return False
    if signature == _imported_signature:
        return False
    if time.time() - signature[0] / 1e9 < SETTLE_SECONDS:
        return False    # still being saved; look again next tick

    with SessionLocal() as db:
        try:
            result = sync_linked_file(db)
        except SyncBusy:
            return False
        except Exception:   # noqa: BLE001 - recorded in the status, retried next tick
            return False
    log.info(
        "SAP sync: %s -> %s new, %s updated, %s reassigned, %s error(s)",
        path.name,
        result.customers_created,
        result.customers_updated,
        result.owners_changed,
        result.error_count,
    )
    return True


def _loop() -> None:
    while not _stop.is_set():
        try:
            check_once()
        except Exception:   # noqa: BLE001 - the watcher must never die
            log.exception("SAP sync check failed")
        _stop.wait(max(5, settings.SAP_SYNC_INTERVAL_SECONDS))


def start() -> None:
    """Start the watcher if a workbook is linked and auto-sync is on."""
    global _thread
    if not settings.SAP_AUTO_SYNC or linked_file() is None:
        return
    if _thread is not None and _thread.is_alive():
        return
    _stop.clear()
    _thread = threading.Thread(target=_loop, name="sap-sync", daemon=True)
    _thread.start()


def stop() -> None:
    _stop.set()


def status() -> dict[str, Any]:
    """What the Super Admin status line shows. Never a filesystem path."""
    path = linked_file()
    exists = False
    saved_at = None
    in_sync = False
    if path is not None:
        try:
            exists = path.exists()
            if exists:
                signature = _signature(path)
                saved_at = datetime.fromtimestamp(signature[0] / 1e9, tz=timezone.utc)
                in_sync = _imported_signature == signature
        except OSError:
            exists = False
    return {
        "linked": path is not None,
        "linked_file_name": path.name if path else None,
        "file_found": exists,
        "file_saved_at": saved_at,
        "auto_sync": bool(settings.SAP_AUTO_SYNC and path),
        "watcher_running": bool(_thread and _thread.is_alive()),
        "interval_seconds": settings.SAP_SYNC_INTERVAL_SECONDS,
        "in_sync": in_sync,
        **_state,
    }
