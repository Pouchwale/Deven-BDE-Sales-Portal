"""Restore, with every safeguard in front of the one destructive step.

Sources: a backup from the history, an uploaded .dump, or an uploaded full
project ZIP (whose database dump can be restored; its CODE is only unpacked
for inspection and download - a web request never replaces the running
application's source).

Order, and the database is only touched at step 5:

  1. validate the source (format, checksum, pg_restore can read it)
  2. confirm the target: the portal's own database, identified by name,
     typed back by the Super Admin, with a password re-check (API layer)
  3. a dump from a DIFFERENT database needs an extra, explicit flag
  4. take a fresh safety backup of the target and VERIFY it - no verified
     safety backup, no restore
  5. pg_restore --single-transaction: any error leaves the database as it was
  6. verify the result; if it does not hold up, put the safety backup back
  7. bring the schema up to this code's migrations, re-apply the backup
     module's own settings, record everything (catalog, activity, audit)

What a single transaction cannot promise: a restore that SUCCEEDS replaces
everything written after the backup was taken. That is the point of a
restore, and why step 4 exists.
"""
from __future__ import annotations

import json
import shutil
import uuid
from pathlib import Path
from typing import Any, Callable

from app.core.config import settings
from app.services.backups import catalog, engine, pgtools
from app.services.backups.project import UnsafeArchive, check_zip, safe_extract, sha256_file

Progress = Callable[[str, int], None]
BACKUP_SETTING_PREFIX = "backup."


class RestoreRefused(ValueError):
    """A precondition failed; nothing was changed. Message is safe to show."""


# ------------------------------------------------------------- uploads
def staging_dir(root: Path, upload_id: str) -> Path:
    try:
        uuid.UUID(upload_id)
    except ValueError:
        raise RestoreRefused("Unknown upload.") from None
    path = (root / "restore-staging" / f"upload-{upload_id}").resolve()
    if not path.is_dir():
        raise RestoreRefused("That upload is no longer available - upload it again.")
    return path


def stage_upload(root: Path, filename: str, chunks, max_bytes: int) -> dict[str, Any]:
    """Save an uploaded file into restore-staging, enforcing the size limit
    while streaming (an oversized file is never fully written)."""
    lower = (filename or "").lower()
    if lower.endswith(".dump"):
        kind = "DATABASE"
    elif lower.endswith(".zip"):
        kind = "FULL"
    else:
        raise RestoreRefused("Upload a PostgreSQL .dump or a full-project .zip.")
    upload_id = str(uuid.uuid4())
    folder = root / "restore-staging" / f"upload-{upload_id}"
    folder.mkdir(parents=True)
    target = folder / ("upload.dump" if kind == "DATABASE" else "upload.zip")
    written = 0
    try:
        with target.open("wb") as out:
            for chunk in chunks:
                written += len(chunk)
                if written > max_bytes:
                    raise RestoreRefused(f"The file is larger than {max_bytes // 1024**2} MB.")
                out.write(chunk)
        if written == 0:
            raise RestoreRefused("The file is empty.")
    except Exception:
        shutil.rmtree(folder, ignore_errors=True)
        raise
    meta = {"upload_id": upload_id, "kind": kind, "original_name": Path(filename).name,
            "size_bytes": written, "sha256": sha256_file(target), "uploaded_at": catalog.now_iso()}
    (folder / "upload.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    return meta


def _upload_meta(root: Path, upload_id: str) -> tuple[Path, dict[str, Any]]:
    folder = staging_dir(root, upload_id)
    return folder, json.loads((folder / "upload.json").read_text(encoding="utf-8"))


# -------------------------------------------------------------- sources
def resolve_dump(root: Path, source: dict[str, Any]) -> tuple[Path, dict[str, Any]]:
    """(dump file, description) for a source. A ZIP is checked and unpacked
    into staging first; only its database dump is ever restored."""
    if source.get("backup_id"):
        record = catalog.get(root, str(source["backup_id"]))
        if record is None:
            raise RestoreRefused("That backup is not in the history.")
        path = catalog.file_path(root, record)
        if not path or not path.exists():
            raise RestoreRefused("That backup's file is missing from storage.")
        if record.get("sha256") and sha256_file(path) != record["sha256"]:
            raise RestoreRefused("That backup's checksum no longer matches - it has been altered or damaged.")
        if record["kind"] == "DATABASE":
            return path, {"label": path.name, "kind": "DATABASE", "backup_id": record["id"],
                          "sha256": record.get("sha256"), "size_bytes": path.stat().st_size,
                          "trusted_checksum": True}
        return _dump_from_zip(root, path, {"label": path.name, "backup_id": record["id"]}, record["id"])
    if source.get("upload_id"):
        folder, meta = _upload_meta(root, str(source["upload_id"]))
        if meta["kind"] == "DATABASE":
            return folder / "upload.dump", {"label": meta["original_name"], "kind": "DATABASE",
                                            "upload_id": meta["upload_id"], "sha256": meta["sha256"],
                                            "size_bytes": meta["size_bytes"], "trusted_checksum": False}
        return _dump_from_zip(root, folder / "upload.zip", {"label": meta["original_name"],
                                                             "upload_id": meta["upload_id"]}, meta["upload_id"])
    raise RestoreRefused("Choose a backup or upload a file to restore.")


def _dump_from_zip(root: Path, zip_path: Path, desc: dict[str, Any], key: str) -> tuple[Path, dict[str, Any]]:
    try:
        check_zip(zip_path)
    except UnsafeArchive as exc:
        raise RestoreRefused(str(exc)) from None
    out = root / "restore-staging" / f"unpacked-{key}"
    if not (out / engine.MANIFEST_NAME).exists():
        shutil.rmtree(out, ignore_errors=True)
        try:
            safe_extract(zip_path, out)
        except UnsafeArchive as exc:
            shutil.rmtree(out, ignore_errors=True)
            raise RestoreRefused(str(exc)) from None
    try:
        manifest = json.loads((out / engine.MANIFEST_NAME).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        raise RestoreRefused("The ZIP has no readable BACKUP_MANIFEST.json - not a GP3 full backup.") from None
    mismatched = [
        entry["path"] for entry in manifest.get("files", [])
        if not (out / entry["path"]).is_file() or sha256_file(out / entry["path"]) != entry["sha256"]
    ]
    dump_rel = manifest.get("database_dump")
    if not dump_rel or not (out / dump_rel).is_file():
        raise RestoreRefused("The ZIP does not contain a database dump.")
    dump_entry = next((e for e in manifest.get("files", []) if e["path"] == dump_rel), None)
    return out / dump_rel, {
        **desc, "kind": "FULL",
        "sha256": dump_entry["sha256"] if dump_entry else None,
        "size_bytes": (out / dump_rel).stat().st_size,
        "trusted_checksum": True,
        "manifest_ok": not mismatched,
        "manifest_mismatches": mismatched[:20],
        "application_commit": manifest.get("application_commit"),
        "project_structure": manifest.get("project_structure"),
        "file_count": manifest.get("file_count"),
        "created_at": manifest.get("created_at"),
    }


def inspect_source(root: Path, source: dict[str, Any]) -> dict[str, Any]:
    """Everything the Super Admin needs before deciding - read only."""
    dump_file, desc = resolve_dump(root, source)
    info = pgtools.inspect(dump_file)
    target = pgtools.portal_target()
    checksum_ok = True
    if desc.get("trusted_checksum") and desc.get("sha256"):
        checksum_ok = sha256_file(dump_file) == desc["sha256"]
    same_source = bool(info.archive_dbname) and info.archive_dbname == target.database
    return {
        "source": desc,
        "dump": info.__dict__,
        "checksum_ok": checksum_ok,
        "target": {
            "database": target.database,
            "identity": target.identity,
            "environment": settings.ENV,
        },
        "same_source": same_source,
        "restorable": info.readable and checksum_ok and desc.get("manifest_ok", True),
    }


# --------------------------------------------------------------- restore
def _snapshot_backup_settings() -> dict[str, str]:
    from sqlalchemy import select

    from app.db.session import SessionLocal
    from app.models.system import AppSetting

    with SessionLocal() as db:
        rows = db.execute(
            select(AppSetting).where(AppSetting.key.like(f"{BACKUP_SETTING_PREFIX}%"))
        ).scalars().all()
        return {row.key: row.value for row in rows}


def _reapply_after_restore(saved: dict[str, str]) -> list[str]:
    """After a restore the database carries the dump's settings. Put back the
    backup module's own (where backups live), bring the schema up to this
    code, and stop the dump's Super Admin env record from resetting a
    password at the next start."""
    from app.db import migrate
    from app.db.session import SessionLocal, engine as db_engine
    from app.services import runtime_settings, super_admin_env

    notes: list[str] = []
    applied = migrate.upgrade(db_engine, verbose=False)
    if applied:
        notes.append(f"Applied {len(applied)} newer migration(s): {', '.join(applied)}")
    with SessionLocal() as db:
        for key, value in saved.items():
            runtime_settings.set_value(db, key, value)
        if super_admin_env.adopt(db) == "adopted":
            notes.append("Super Admin environment values adopted (no password changed).")
        db.commit()
    return notes


def _verify_restored(target: pgtools.Target, info: pgtools.DumpInfo) -> tuple[bool, str]:
    tables = int(pgtools.scalar(target, "select count(*) from information_schema.tables where table_schema='public' and table_type='BASE TABLE'"))
    users = int(pgtools.scalar(target, "select count(*) from users"))
    version = pgtools.scalar(target, "select coalesce(max(version), '') from schema_migrations")
    ok = tables >= info.tables and users > 0
    return ok, f"{tables} tables (dump had {info.tables}), {users} users, schema version {version or 'none'}"


def run_restore(
    root: Path,
    source: dict[str, Any],
    *,
    actor: dict | None,
    allow_other_source: bool,
    progress: Progress,
) -> dict[str, Any]:
    from app.db.session import engine as db_engine

    progress("Validating archive", 5)
    dump_file, desc = resolve_dump(root, source)
    progress("Inspecting database dump", 12)
    info = pgtools.inspect(dump_file)
    if not info.readable:
        raise RestoreRefused(info.problem or "The dump cannot be read.")
    if desc.get("trusted_checksum") and desc.get("sha256") and sha256_file(dump_file) != desc["sha256"]:
        raise RestoreRefused("The dump's checksum does not match its manifest.")
    if desc.get("manifest_ok") is False:
        raise RestoreRefused("The archive's files do not match its manifest.")
    target = pgtools.portal_target()
    if info.archive_dbname and info.archive_dbname != target.database and not allow_other_source:
        raise RestoreRefused(
            f"This dump came from database '{info.archive_dbname}', not '{target.database}'. "
            "Restoring another environment's data needs the extra confirmation."
        )
    tool_major = pgtools.tool_major("pg_restore") or 0
    if tool_major < pgtools.server_major(target):
        raise RestoreRefused("pg_restore is older than the database server.")

    progress("Creating safety backup", 20)
    safety = engine.create_database_backup(root, actor=actor, source="pre-restore")
    if safety["status"] != "VERIFIED":
        raise RestoreRefused(
            f"The safety backup of the current database could not be verified ({safety.get('error')}). "
            "Nothing was changed."
        )
    saved_settings = _snapshot_backup_settings()

    progress("Preparing restore target", 40)
    db_engine.dispose()  # release the portal's pooled connections and their locks

    progress("Restoring schema and data", 50)
    try:
        pgtools.restore(target, dump_file)
    except pgtools.PgToolError as exc:
        return {"ok": False, "error": f"The restore was rolled back; the database is unchanged. {exc}",
                "safety_backup_id": safety["id"], "rolled_back": True, "source": desc}

    progress("Verifying integrity", 80)
    try:
        ok, detail = _verify_restored(target, info)
    except pgtools.PgToolError as exc:
        ok, detail = False, str(exc)
    if not ok:
        progress("Verification failed - putting the safety backup back", 85)
        safety_path = catalog.file_path(root, safety)
        try:
            pgtools.restore(target, safety_path)
            back = "The previous database was put back from the safety backup."
        except pgtools.PgToolError as exc:
            back = f"PUTTING THE SAFETY BACKUP BACK FAILED ({exc}). Restore {safety_path.name} by hand."
        return {"ok": False, "error": f"The restored database failed verification ({detail}). {back}",
                "safety_backup_id": safety["id"], "rolled_back": True, "source": desc}

    progress("Finishing", 92)
    notes = _reapply_after_restore(saved_settings)
    return {"ok": True, "detail": detail, "notes": notes, "safety_backup_id": safety["id"],
            "source": desc, "target": target.identity, "archive_dbname": info.archive_dbname}
