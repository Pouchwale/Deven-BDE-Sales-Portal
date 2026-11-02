"""Back-Up & Restore - Super Admin only, every route.

The role check is the SuperAdminUser dependency on each route (not the menu
hiding the page). Destructive actions - restore and delete - also need the
Super Admin's password typed again. Nothing returned here contains a
connection string or credential.
"""
from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, File, Query, Request, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from app.core.config import settings
from app.core.constants import AuditAction, EntityType
from app.core.deps import DbSession, SuperAdminUser, client_ip
from app.core.errors import conflict, invalid, not_found, unauthorized
from app.core.security import verify_password
from app.services import audit, runtime_settings
from app.services.backups import catalog, engine, jobs, pgtools, restore, storage

router = APIRouter(prefix="/admin/backups", tags=["backups"])

SETTING_KEYS = (
    "backup.storage_path", "backup.max_storage_gb", "backup.alert_percent",
    "backup.auto_daily", "backup.auto_time", "backup.keep_daily",
    "backup.keep_weekly", "backup.keep_monthly",
)


def _actor(user) -> dict[str, Any]:  # noqa: ANN001
    return {"id": str(user.id), "name": user.name}


def _root(db) -> Path:  # noqa: ANN001
    try:
        return storage.root(db)
    except storage.StorageError as exc:
        raise conflict(str(exc)) from None


def _audit(db, actor, action, *, backup_id=None, ip=None, **after) -> None:  # noqa: ANN001
    audit.record(
        db, actor_id=actor.id if actor else None, action=action, entity_type=EntityType.BACKUP,
        entity_id=uuid.UUID(backup_id) if backup_id else None, after=after or None, ip_address=ip,
    )


def _audit_job(job: dict[str, Any], success: str, failure: str, **extra) -> None:
    """Called from the worker thread when a job ends. A NEW session: after a
    restore the database is the restored one, and this is the first entry the
    restored audit trail gets about how it came to be."""
    from app.db.session import SessionLocal

    result = job.get("result") or {}
    ok = job["status"] == "SUCCEEDED"
    actor_id = (job.get("actor") or {}).get("id")
    with SessionLocal() as db:
        audit.record(
            db,
            actor_id=uuid.UUID(actor_id) if actor_id else None,
            action=success if ok else failure,
            entity_type=EntityType.BACKUP,
            entity_id=uuid.UUID(result["backup_id"]) if result.get("backup_id") else None,
            after={
                "job": job["id"], "result": "success" if ok else "failure",
                "reason": None if ok else (job.get("error") or "")[:300], **extra,
            },
        )
        db.commit()


def _require_password(actor, password: str) -> None:  # noqa: ANN001
    if not password or not verify_password(password, actor.hashed_password):
        raise unauthorized("Your password is incorrect. It is required for this action.")


# ----------------------------------------------------------------- reads
@router.get("/overview")
def overview(actor: SuperAdminUser, db: DbSession) -> dict[str, Any]:
    status = storage.status(db)
    config = runtime_settings.get_many(db, list(SETTING_KEYS))
    target = pgtools.portal_target()
    tools: dict[str, Any] = {"pg_dump": None, "server": None, "compatible": False, "problem": None}
    try:
        tools["pg_dump"] = pgtools.tool_major("pg_dump")
        tools["server"] = pgtools.server_version(target)
        server_major = int(tools["server"].split(".")[0].split()[0])
        tools["compatible"] = bool(tools["pg_dump"]) and tools["pg_dump"] >= server_major
        if not tools["compatible"]:
            tools["problem"] = "pg_dump is missing or older than the database server."
    except pgtools.PgToolError as exc:
        tools["problem"] = str(exc)

    records: list[dict] = []
    activity: list[dict] = []
    active_job = None
    if status.available:
        root = Path(status.path)
        records = [r for r in catalog.all_records(root) if not r.get("deleted_at")]
        activity = catalog.activity(root, 200)
        active_job = jobs.active(root)
    verified = [r for r in records if r["status"] == "VERIFIED"]
    latest_restore = next((a for a in activity if a["action"] in ("BACKUP_RESTORED", "BACKUP_RESTORE_FAILED")), None)
    used = status.used_bytes
    alert = None
    if status.max_bytes and used >= status.max_bytes * int(config["backup.alert_percent"]) / 100:
        alert = f"Backups use {used / status.max_bytes:.0%} of the {config['backup.max_storage_gb']} GB allowed."
    return {
        "storage": {
            "configured": status.configured, "path": status.path, "label": storage.label(status.path),
            "available": status.available, "problem": status.problem, "used_bytes": used,
            "free_bytes": status.free_bytes, "total_bytes": status.total_bytes,
            "max_bytes": status.max_bytes, "alert": alert,
        },
        "tools": tools,
        "host": storage.host(),
        "target": {"database": target.database, "identity": target.identity, "environment": settings.ENV},
        "counts": {
            "total": len(records),
            "database": sum(r["kind"] == "DATABASE" for r in records),
            "full": sum(r["kind"] == "FULL" for r in records),
            "verified": len(verified),
            "failed": sum(r["status"] == "FAILED" for r in records),
            "missing": sum(not r.get("file_present") for r in records if r["status"] == "VERIFIED"),
        },
        "latest_backup": verified[0] if verified else None,
        "latest_restore": latest_restore,
        "active_job": active_job,
        "settings": config,
    }


@router.get("")
def list_backups(actor: SuperAdminUser, db: DbSession, include_deleted: bool = Query(False)) -> list[dict]:
    status = storage.status(db)
    if not status.available:
        return []
    records = catalog.all_records(Path(status.path))
    return records if include_deleted else [r for r in records if not r.get("deleted_at")]


@router.get("/activity")
def activity(actor: SuperAdminUser, db: DbSession, limit: int = Query(100, ge=1, le=500)) -> list[dict]:
    status = storage.status(db)
    return catalog.activity(Path(status.path), limit) if status.available else []


@router.get("/jobs/{job_id}")
def job_status(job_id: str, actor: SuperAdminUser, db: DbSession) -> dict[str, Any]:
    job = jobs.get(_root(db), job_id)
    if job is None:
        raise not_found("No such job.")
    return job


# ---------------------------------------------------------------- create
class CreateBackup(BaseModel):
    type: Literal["database", "full"]


@router.post("")
def create_backup(payload: CreateBackup, request: Request, actor: SuperAdminUser, db: DbSession) -> dict[str, Any]:
    root = _root(db)
    status = storage.status(db)
    if status.max_bytes and status.used_bytes >= status.max_bytes:
        raise conflict("Backups have reached the storage allowance. Delete old ones or raise the limit.")
    who = _actor(actor)
    kind = "FULL" if payload.type == "full" else "DATABASE"

    def work(progress):
        if kind == "FULL":
            record = engine.create_full_backup(root, actor=who, progress=progress)
        else:
            record = engine.create_database_backup(root, actor=who, progress=progress)
        ok = record["status"] == "VERIFIED"
        catalog.log(root, "BACKUP_CREATED" if ok else "BACKUP_FAILED", actor=who, kind=kind,
                    backup_id=record["id"], file=record.get("file"), error=record.get("error"),
                    storage=storage.label(root))
        return {"ok": ok, "backup_id": record["id"], "error": record.get("error")}

    try:
        job = jobs.start(
            root, kind=f"BACKUP_{kind}", title="Full project backup" if kind == "FULL" else "Database backup",
            actor=who, work=work,
            on_done=lambda j: _audit_job(j, AuditAction.BACKUP_CREATED, AuditAction.BACKUP_FAILED,
                                         backup_type=kind, storage=storage.label(root)),
        )
    except jobs.Busy as exc:
        raise conflict(str(exc)) from None
    return job


@router.post("/{backup_id}/verify")
def verify_backup(backup_id: str, actor: SuperAdminUser, db: DbSession,
                  test_restore: bool = Query(False)) -> dict[str, Any]:
    root = _root(db)
    record = catalog.get(root, backup_id)
    if record is None:
        raise not_found("No such backup.")
    who = _actor(actor)

    def work(progress):
        progress("Verifying backup", 30)
        verification = engine.verify(root, record, test_restore=test_restore)
        record["verification"] = verification
        record["verified_at"] = catalog.now_iso()
        record["status"] = "VERIFIED" if verification["ok"] else "FAILED"
        record.pop("file_present", None)
        catalog.save(root, record)
        catalog.log(root, "BACKUP_VERIFIED", actor=who, backup_id=backup_id, ok=verification["ok"])
        return {"ok": verification["ok"], "backup_id": backup_id,
                "error": None if verification["ok"] else "Verification failed"}

    try:
        return jobs.start(root, kind="VERIFY", title="Verify backup", actor=who, work=work,
                          on_done=lambda j: _audit_job(j, AuditAction.BACKUP_VERIFIED, AuditAction.BACKUP_VERIFIED))
    except jobs.Busy as exc:
        raise conflict(str(exc)) from None


@router.get("/{backup_id}/download")
def download(backup_id: str, request: Request, actor: SuperAdminUser, db: DbSession) -> FileResponse:
    root = _root(db)
    record = catalog.get(root, backup_id)
    path = catalog.file_path(root, record) if record else None
    if not path or not path.exists():
        raise not_found("That backup's file is not in storage.")
    _audit(db, actor, AuditAction.BACKUP_DOWNLOADED, backup_id=backup_id, ip=client_ip(request),
           backup_type=record["kind"])
    db.commit()
    catalog.log(root, "BACKUP_DOWNLOADED", actor=_actor(actor), backup_id=backup_id)
    return FileResponse(path, filename=path.name, media_type="application/octet-stream",
                        headers={"Cache-Control": "no-store"})


class WithPassword(BaseModel):
    password: str = Field(min_length=1)


@router.delete("/{backup_id}")
def delete_backup(backup_id: str, payload: WithPassword, request: Request,
                  actor: SuperAdminUser, db: DbSession) -> dict[str, Any]:
    _require_password(actor, payload.password)
    root = _root(db)
    record = catalog.get(root, backup_id)
    if record is None or record.get("deleted_at"):
        raise not_found("No such backup.")
    running = jobs.active(root)
    if running:
        raise conflict("A backup or restore is running. Delete after it finishes.")
    others = [r for r in catalog.all_records(root)
              if r["id"] != backup_id and r["status"] == "VERIFIED" and r.get("file_present") and not r.get("deleted_at")]
    if record["status"] == "VERIFIED" and not others:
        raise conflict("This is the only verified backup. Create another before deleting it.")
    path = catalog.file_path(root, record)
    if path and path.exists():
        path.unlink()
        sidecar = path.with_suffix(".manifest.json")
        if sidecar.exists():
            sidecar.unlink()
    record.pop("file_present", None)
    record.update(deleted_at=catalog.now_iso(), deleted_reason="deleted by Super Admin")
    catalog.save(root, record)
    catalog.log(root, "BACKUP_DELETED", actor=_actor(actor), backup_id=backup_id, kind=record["kind"])
    _audit(db, actor, AuditAction.BACKUP_DELETED, backup_id=backup_id, ip=client_ip(request),
           backup_type=record["kind"])
    db.commit()
    return {"message": "Backup deleted. Its record stays in the history."}


# --------------------------------------------------------------- settings
class StorageTest(BaseModel):
    path: str


@router.post("/storage/test")
def test_storage(payload: StorageTest, actor: SuperAdminUser) -> dict[str, Any]:
    try:
        resolved = storage.validate_destination(payload.path)
    except storage.StorageError as exc:
        return {"ok": False, "problem": str(exc)}
    return {"ok": True, "path": str(resolved), "message": "Folder is writable and ready."}


class BackupSettings(BaseModel):
    storage_path: str | None = None
    max_storage_gb: float | None = Field(default=None, ge=0, le=100000)
    alert_percent: int | None = Field(default=None, ge=1, le=100)
    auto_daily: bool | None = None
    auto_time: str | None = Field(default=None, pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    keep_daily: int | None = Field(default=None, ge=1, le=365)
    keep_weekly: int | None = Field(default=None, ge=0, le=104)
    keep_monthly: int | None = Field(default=None, ge=0, le=120)


@router.patch("/settings")
def save_settings(payload: BackupSettings, request: Request, actor: SuperAdminUser, db: DbSession) -> dict[str, Any]:
    changes = payload.model_dump(exclude_none=True)
    if "storage_path" in changes:
        try:
            changes["storage_path"] = str(storage.validate_destination(changes["storage_path"]))
        except storage.StorageError as exc:
            raise invalid(str(exc), field="storage_path") from None
    for key, value in changes.items():
        runtime_settings.set_value(db, f"backup.{key}", value, actor_id=actor.id)
    _audit(db, actor, AuditAction.BACKUP_STORAGE_CHANGED, ip=client_ip(request),
           changed=sorted(changes), storage=storage.label(changes.get("storage_path")) if "storage_path" in changes else None)
    db.commit()
    if "storage_path" in changes:
        catalog.log(Path(changes["storage_path"]), "BACKUP_STORAGE_CHANGED", actor=_actor(actor))
    return runtime_settings.get_many(db, list(SETTING_KEYS))


# ---------------------------------------------------------------- restore
@router.post("/restore/upload")
def upload_for_restore(actor: SuperAdminUser, db: DbSession, file: UploadFile = File(...)) -> dict[str, Any]:
    root = _root(db)
    max_bytes = settings.BACKUP_MAX_UPLOAD_MB * 1024**2

    def chunks():
        while True:
            chunk = file.file.read(1024 * 1024)
            if not chunk:
                return
            yield chunk

    try:
        meta = restore.stage_upload(root, file.filename or "", chunks(), max_bytes)
        inspection = restore.inspect_source(root, {"upload_id": meta["upload_id"]})
    except restore.RestoreRefused as exc:
        raise invalid(str(exc)) from None
    catalog.log(root, "RESTORE_FILE_UPLOADED", actor=_actor(actor), kind=meta["kind"],
                name=meta["original_name"], size_bytes=meta["size_bytes"])
    return {"upload": meta, "inspection": inspection}


class RestoreSource(BaseModel):
    backup_id: str | None = None
    upload_id: str | None = None


@router.post("/restore/inspect")
def inspect_for_restore(payload: RestoreSource, actor: SuperAdminUser, db: DbSession) -> dict[str, Any]:
    try:
        return restore.inspect_source(_root(db), payload.model_dump(exclude_none=True))
    except restore.RestoreRefused as exc:
        raise invalid(str(exc)) from None


class RunRestore(RestoreSource):
    password: str = Field(min_length=1)
    confirm_database: str
    allow_other_source: bool = False


@router.post("/restore")
def run_restore(payload: RunRestore, request: Request, actor: SuperAdminUser, db: DbSession) -> dict[str, Any]:
    _require_password(actor, payload.password)
    root = _root(db)
    target = pgtools.portal_target()
    if payload.confirm_database.strip() != target.database:
        raise invalid(
            f"Type the target database's name exactly ({target.database}) to confirm.",
            field="confirm_database",
        )
    source = payload.model_dump(include={"backup_id", "upload_id"}, exclude_none=True)
    try:
        inspection = restore.inspect_source(root, source)
    except restore.RestoreRefused as exc:
        raise invalid(str(exc)) from None
    if not inspection["restorable"]:
        raise invalid("This file cannot be restored: " + (inspection["dump"].get("problem") or "checks failed"))
    if not inspection["same_source"] and not payload.allow_other_source:
        raise conflict(
            f"This backup came from '{inspection['dump'].get('archive_dbname')}', not "
            f"'{target.database}'. Tick the extra confirmation to restore another environment's data."
        )
    who = _actor(actor)
    _audit(db, actor, AuditAction.BACKUP_RESTORE_STARTED, backup_id=source.get("backup_id"),
           ip=client_ip(request), target=target.identity, source=inspection["source"].get("label"))
    db.commit()
    catalog.log(root, "BACKUP_RESTORE_STARTED", actor=who, target=target.identity,
                source=inspection["source"].get("label"))

    def work(progress):
        result = restore.run_restore(root, source, actor=who,
                                     allow_other_source=payload.allow_other_source, progress=progress)
        catalog.log(root, "BACKUP_RESTORED" if result.get("ok") else "BACKUP_RESTORE_FAILED", actor=who,
                    target=target.identity, source=inspection["source"].get("label"),
                    safety_backup_id=result.get("safety_backup_id"), error=result.get("error"),
                    detail=result.get("detail"))
        return result

    try:
        return jobs.start(
            root, kind="RESTORE", title="Restore database", actor=who, work=work,
            on_done=lambda j: _audit_job(j, AuditAction.BACKUP_RESTORED, AuditAction.BACKUP_RESTORE_FAILED,
                                         target=target.identity, source=inspection["source"].get("label")),
        )
    except jobs.Busy as exc:
        raise conflict(str(exc)) from None
