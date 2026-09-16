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
"""
from __future__ import annotations

import logging
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.session import SessionLocal
from app.models.org import User
from app.services.customer_source import FileCustomerSource
from app.services.sap_import import ImportResult, import_source

log = logging.getLogger("app.sap_sync")

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


def run_import(db: Session, path: Path, *, actor: User | None = None) -> ImportResult:
    """Import one file under the shared lock. Commits, or rolls back and raises."""
    if not _lock.acquire(timeout=120):
        raise SyncBusy("Another SAP import is still running. Try again in a moment.")
    try:
        try:
            result = import_source(db, FileCustomerSource(path), actor=actor)
            db.commit()
        except Exception:
            db.rollback()
            raise
        return result
    finally:
        _lock.release()


def sync_linked_file(db: Session, *, actor: User | None = None) -> ImportResult:
    """Import the linked workbook now and record the outcome in the status."""
    global _imported_signature
    path = linked_file()
    if path is None:
        raise FileNotFoundError("No SAP workbook is linked (SAP_DATA_FILE is blank).")
    if not path.exists():
        _record_error(f"Linked SAP workbook not found: {path}")
        raise FileNotFoundError(f"Linked SAP workbook not found: {path}")

    signature = _signature(path)
    try:
        result = run_import(db, path, actor=actor)
    except SyncBusy:
        raise
    except Exception as exc:
        _record_error(f"{type(exc).__name__}: {exc}")
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


def _record_error(message: str) -> None:
    _state.update(last_error=message[:500], last_error_at=_now())
    log.warning("SAP sync: %s", message)


def check_once() -> bool:
    """One watcher tick. True if an import ran."""
    _state["last_checked_at"] = _now()
    path = linked_file()
    if path is None:
        return False
    try:
        if not path.exists():
            _record_error(f"Linked SAP workbook not found: {path}")
            return False
        signature = _signature(path)
    except OSError as exc:
        _record_error(f"Cannot read linked SAP workbook: {exc}")
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
    path = linked_file()
    exists = bool(path and path.exists())
    saved_at = None
    if exists and path is not None:
        saved_at = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
    return {
        "linked_file": str(path) if path else None,
        "linked_file_name": path.name if path else None,
        "file_found": exists,
        "file_saved_at": saved_at,
        "auto_sync": bool(settings.SAP_AUTO_SYNC and path),
        "watcher_running": bool(_thread and _thread.is_alive()),
        "interval_seconds": settings.SAP_SYNC_INTERVAL_SECONDS,
        "in_sync": bool(
            exists and path is not None and _imported_signature == _signature(path)
        ),
        **_state,
    }
