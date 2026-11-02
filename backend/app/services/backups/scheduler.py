"""Optional daily database backup, run by the app itself (so it works on
Render, where there is no Windows Task Scheduler and no browser is open).

Off by default. On the owner's PC the Windows task "BDE Portal - production
backup" already backs up the LIVE database every night; this schedule backs
up whichever database THIS app runs on, so the two never compete.

Retention prunes only SCHEDULED backups, keeps the newest per day / week /
month as configured, and never removes the newest verified backup of any
kind - so it cannot leave the system without a recovery point.
"""
from __future__ import annotations

import asyncio
import json
from datetime import datetime
from pathlib import Path

from app.core.logging import get_logger
from app.services.backups import catalog, engine, jobs, storage

log = get_logger("app.backups")
_task: asyncio.Task[None] | None = None
CHECK_EVERY_SECONDS = 300


def _state_file(root: Path) -> Path:
    return root / "backup-logs" / "scheduler.json"


def _read_state(root: Path) -> dict:
    path = _state_file(root)
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def due(now_ist: datetime, auto_time: str, last_run_date: str | None) -> bool:
    try:
        hour, minute = (int(part) for part in auto_time.split(":", 1))
    except ValueError:
        hour, minute = 2, 30
    if last_run_date == now_ist.date().isoformat():
        return False
    return (now_ist.hour, now_ist.minute) >= (hour, minute)


def prune(root: Path, keep_daily: int, keep_weekly: int, keep_monthly: int) -> list[str]:
    """Delete scheduled backups outside the retention window. Returns ids."""
    records = [r for r in catalog.all_records(root) if not r.get("deleted_at")]
    verified = [r for r in records if r["status"] == "VERIFIED" and r.get("file_present")]
    protected = {verified[0]["id"]} if verified else set()  # newest verified, any kind
    scheduled = [r for r in verified if r["source"] == "scheduled" and r["kind"] == "DATABASE"]

    keep: set[str] = set()
    seen_days, seen_weeks, seen_months = [], [], []
    for record in scheduled:  # newest first
        stamp = datetime.fromisoformat(record["created_at"])
        day, week, month = stamp.date(), stamp.isocalendar()[:2], (stamp.year, stamp.month)
        if day not in seen_days and len(seen_days) < keep_daily:
            seen_days.append(day)
            keep.add(record["id"])
        if week not in seen_weeks and len(seen_weeks) < keep_weekly:
            seen_weeks.append(week)
            keep.add(record["id"])
        if month not in seen_months and len(seen_months) < keep_monthly:
            seen_months.append(month)
            keep.add(record["id"])

    removed = []
    for record in scheduled:
        if record["id"] in keep or record["id"] in protected:
            continue
        path = catalog.file_path(root, record)
        if path and path.exists():
            path.unlink()
            sidecar = path.with_suffix(".manifest.json")
            if sidecar.exists():
                sidecar.unlink()
        record.pop("file_present", None)
        record["deleted_at"] = catalog.now_iso()
        record["deleted_reason"] = "retention"
        catalog.save(root, record)
        removed.append(record["id"])
    if removed:
        catalog.log(root, "RETENTION_PRUNED", removed=len(removed))
    return removed


def run_once(now_ist: datetime | None = None) -> str:
    """One scheduler tick. Returns what happened (for logs and tests)."""
    from app.db.session import SessionLocal
    from app.services import runtime_settings

    with SessionLocal() as db:
        config = runtime_settings.get_many(db, [
            "backup.auto_daily", "backup.auto_time", "backup.keep_daily",
            "backup.keep_weekly", "backup.keep_monthly",
        ])
        if not config["backup.auto_daily"]:
            return "off"
        try:
            root = storage.root(db)
        except storage.StorageError as exc:
            log.error("Scheduled backup skipped: %s", exc)
            return "no-storage"
    now_ist = now_ist or datetime.now(engine.IST)
    state = _read_state(root)
    if not due(now_ist, str(config["backup.auto_time"]), state.get("last_run_date")):
        return "not-due"
    if jobs.active(root):
        return "busy"

    def work(progress):
        record = engine.create_database_backup(root, actor=None, source="scheduled", progress=progress)
        ok = record["status"] == "VERIFIED"
        catalog.log(root, "BACKUP_CREATED" if ok else "BACKUP_FAILED", kind="DATABASE",
                    backup_id=record["id"], source="scheduled", error=record.get("error"))
        removed = prune(root, int(config["backup.keep_daily"]), int(config["backup.keep_weekly"]),
                        int(config["backup.keep_monthly"])) if ok else []
        return {"ok": ok, "backup_id": record["id"], "error": record.get("error"), "pruned": len(removed)}

    _state_file(root).write_text(json.dumps({"last_run_date": now_ist.date().isoformat(),
                                              "last_run_at": catalog.now_iso()}), encoding="utf-8")
    jobs.start(root, kind="BACKUP_DATABASE", title="Scheduled database backup", actor=None, work=work)
    return "started"


async def _loop() -> None:
    while True:
        try:
            await asyncio.to_thread(run_once)
        except Exception:  # noqa: BLE001 - the schedule must never crash the app
            log.exception("Backup scheduler tick failed")
        await asyncio.sleep(CHECK_EVERY_SECONDS)


def start() -> None:
    global _task
    if _task is None or _task.done():
        _task = asyncio.get_running_loop().create_task(_loop())


async def stop() -> None:
    global _task
    if _task is not None:
        _task.cancel()
        try:
            await _task
        except (asyncio.CancelledError, Exception):  # noqa: BLE001
            pass
        _task = None
