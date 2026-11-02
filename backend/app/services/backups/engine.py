"""Create and verify both kinds of backup.

A backup is marked VERIFIED only when every check passes - never merely
because pg_dump exited 0:

    exists and non-empty · SHA-256 matches the catalog · (ZIP) archive intact,
    manifest matches every file it lists · (dump) pg_restore can read its
    table of contents and it holds table data · optional test restore
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import uuid
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

from app.core.config import settings
from app.services.backups import catalog, pgtools
from app.services.backups.project import REPO_ROOT, check_zip, project_files, sha256_file

Progress = Callable[[str, int], None]
IST = timezone(timedelta(hours=5, minutes=30))
MANIFEST_NAME = "BACKUP_MANIFEST.json"
MANIFEST_VERSION = 1


def _stamp() -> str:
    return datetime.now(IST).strftime("%Y%m%d_%H%M%S")


def _unique(folder: Path, stem: str, suffix: str) -> Path:
    """Never overwrite: a second backup in the same second gets _2, _3..."""
    folder.mkdir(parents=True, exist_ok=True)
    candidate = folder / f"{stem}{suffix}"
    n = 2
    while candidate.exists():
        candidate = folder / f"{stem}_{n}{suffix}"
        n += 1
    return candidate


def git_commit() -> str | None:
    commit = os.environ.get("RENDER_GIT_COMMIT", "").strip()
    if commit:
        return commit[:12]
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--short=12", "HEAD"], cwd=REPO_ROOT,
            capture_output=True, text=True, timeout=10,
        )
        return out.stdout.strip() or None
    except (OSError, subprocess.SubprocessError):
        return None


def _check_tools(target: pgtools.Target) -> str:
    """Server version; refuses a pg_dump older than the server (it cannot dump
    a newer server correctly)."""
    version = pgtools.server_version(target)
    server_major = int(version.split(".")[0].split()[0])
    tool_major = pgtools.tool_major("pg_dump")
    if tool_major is None:
        raise pgtools.PgToolError("pg_dump was not found on this server.")
    if tool_major < server_major:
        raise pgtools.PgToolError(
            f"pg_dump {tool_major} is older than the database (PostgreSQL {server_major}). "
            "Install client tools of the same version or newer."
        )
    return version


def _finish(root: Path, record: dict[str, Any], verification: dict[str, Any]) -> dict[str, Any]:
    record["verification"] = verification
    record["verified_at"] = catalog.now_iso()
    record["status"] = "VERIFIED" if verification["ok"] else "FAILED"
    if not verification["ok"]:
        record["error"] = "; ".join(c["detail"] for c in verification["checks"] if not c["ok"])
    record["finished_at"] = catalog.now_iso()
    catalog.save(root, record)
    return record


def _fail(root: Path, record: dict[str, Any], message: str) -> dict[str, Any]:
    record["status"] = "FAILED"
    record["error"] = message
    record["finished_at"] = catalog.now_iso()
    catalog.save(root, record)
    return record


# ---------------------------------------------------------- database only
def create_database_backup(
    root: Path, *, actor: dict | None, source: str = "manual", progress: Progress = lambda *_: None
) -> dict[str, Any]:
    record = catalog.new_record(root, kind="DATABASE", source=source, created_by=actor)
    target = pgtools.portal_target()
    try:
        progress("Connecting to PostgreSQL", 10)
        version = _check_tools(target)
        folder = "restore-staging" if source == "pre-restore" else "database-only"
        out = _unique(root / folder, f"GP3_Database_{_stamp()}", ".dump")
        name = out.name
        progress("Exporting schema and data", 30)
        pgtools.dump(target, out)
        progress("Calculating checksums", 70)
        record.update(
            file=f"{folder}/{name}", size_bytes=out.stat().st_size, sha256=sha256_file(out),
            pg_version=version, database=target.identity, git_commit=git_commit(),
        )
        catalog.save(root, record)
        progress("Verifying backup", 85)
        verification = verify(root, record)
        _write_sidecar(root, record)
        progress("Saving to storage", 95)
        return _finish(root, record, verification)
    except Exception as exc:  # noqa: BLE001 - recorded on the backup, never lost
        return _fail(root, record, _safe_message(exc))


def _write_sidecar(root: Path, record: dict[str, Any]) -> None:
    """The manifest that travels with a database-only dump."""
    path = catalog.file_path(root, record)
    if not path:
        return
    sidecar = path.with_suffix(".manifest.json")
    sidecar.write_text(json.dumps({
        "manifest_version": MANIFEST_VERSION,
        "backup_id": record["id"],
        "backup_type": "DATABASE",
        "created_at": record["created_at"],
        "database": record["database"],
        "postgres_version": record["pg_version"],
        "dump_format": "PostgreSQL custom (pg_dump -Fc), no owner, no ACL",
        "file": path.name,
        "size_bytes": record["size_bytes"],
        "sha256": record["sha256"],
        "verification": "VERIFIED" if (record.get("verification") or {}).get("ok") else "PENDING",
    }, indent=2), encoding="utf-8")


# ------------------------------------------------------------ full project
def create_full_backup(
    root: Path, *, actor: dict | None, progress: Progress = lambda *_: None
) -> dict[str, Any]:
    record = catalog.new_record(root, kind="FULL", source="manual", created_by=actor)
    target = pgtools.portal_target()
    work = root / "restore-staging" / f"build-{record['id']}"
    try:
        progress("Preparing backup", 5)
        stamp = _stamp()
        progress("Connecting to PostgreSQL", 10)
        version = _check_tools(target)
        dump_name = f"GP3_Database_{stamp}.dump"
        dump_file = work / dump_name
        progress("Exporting schema and data", 20)
        pgtools.dump(target, dump_file)

        progress("Calculating checksums", 45)
        files = project_files()
        listed = [
            {"path": arcname, "size_bytes": path.stat().st_size, "sha256": sha256_file(path)}
            for path, arcname in files
        ]
        dump_entry = {
            "path": f"database/{dump_name}", "size_bytes": dump_file.stat().st_size,
            "sha256": sha256_file(dump_file),
        }
        manifest = {
            "manifest_version": MANIFEST_VERSION,
            "backup_id": record["id"],
            "backup_type": "FULL",
            "created_at": record["created_at"],
            "application_commit": git_commit(),
            "postgres_version": version,
            "database": target.identity,
            "database_dump": dump_entry["path"],
            "project_structure": sorted({f["path"].split("/")[0] for f in listed}),
            "files": [*listed, dump_entry],
            "file_count": len(listed) + 1,
            "excluded": "secrets (.env, keys), node_modules, .venv, caches, logs, data, backups",
            "verification": "Verified after writing; see the portal's backup history.",
        }

        progress("Creating ZIP archive", 60)
        out = _unique(root / "full-project", f"GP3_Full_Backup_{stamp}", ".zip")
        name = out.name
        with zipfile.ZipFile(out, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
            for path, arcname in files:
                archive.write(path, arcname)
            archive.write(dump_file, dump_entry["path"])
            archive.writestr(MANIFEST_NAME, json.dumps(manifest, indent=2))

        progress("Calculating checksums", 80)
        record.update(
            file=f"full-project/{name}", size_bytes=out.stat().st_size, sha256=sha256_file(out),
            pg_version=version, database=target.identity, git_commit=manifest["application_commit"],
        )
        catalog.save(root, record)
        progress("Verifying backup", 88)
        verification = verify(root, record)
        progress("Saving to storage", 97)
        return _finish(root, record, verification)
    except Exception as exc:  # noqa: BLE001
        return _fail(root, record, _safe_message(exc))
    finally:
        shutil.rmtree(work, ignore_errors=True)


# ------------------------------------------------------------- verification
def _check(checks: list, name: str, ok: bool, detail: str) -> bool:
    checks.append({"name": name, "ok": bool(ok), "detail": detail})
    return bool(ok)


def verify(root: Path, record: dict[str, Any], *, test_restore: bool = False) -> dict[str, Any]:
    checks: list[dict[str, Any]] = []
    path = catalog.file_path(root, record)
    if not _check(checks, "exists", bool(path and path.exists()), "File is present" if path and path.exists() else "File is missing"):
        return {"ok": False, "checks": checks, "at": catalog.now_iso()}
    size = path.stat().st_size
    _check(checks, "non_empty", size > 0, f"{size:,} bytes")
    digest = sha256_file(path)
    _check(checks, "sha256", record.get("sha256") in (None, digest),
           "Checksum matches" if record.get("sha256") in (None, digest) else "Checksum does NOT match the catalog")

    if record["kind"] == "DATABASE":
        info = pgtools.inspect(path)
        _check(checks, "dump_readable", info.readable,
               f"{info.table_data} tables with data, {info.indexes} indexes, {info.constraints} constraints"
               if info.readable else (info.problem or "Unreadable"))
        dump_for_test = path if info.readable else None
    else:
        dump_for_test = None
        try:
            check_zip(path)
            _check(checks, "zip_integrity", True, "Archive intact")
        except Exception as exc:  # noqa: BLE001
            _check(checks, "zip_integrity", False, str(exc))
            return {"ok": False, "checks": checks, "at": catalog.now_iso()}
        scratch = root / "restore-staging" / f"verify-{uuid.uuid4().hex}"
        try:
            with zipfile.ZipFile(path) as archive:
                manifest = json.loads(archive.read(MANIFEST_NAME))
                names = set(archive.namelist())
                mismatched = []
                for entry in manifest.get("files", []):
                    if entry["path"] not in names:
                        mismatched.append(entry["path"])
                        continue
                    digest_entry = _sha256_member(archive, entry["path"])
                    if digest_entry != entry["sha256"]:
                        mismatched.append(entry["path"])
                unlisted = names - {e["path"] for e in manifest.get("files", [])} - {MANIFEST_NAME}
                _check(checks, "manifest_matches", not mismatched and not unlisted,
                       f"{len(manifest.get('files', []))} files match the manifest"
                       if not mismatched and not unlisted
                       else f"{len(mismatched)} differ, {len(unlisted)} not listed")
                dump_member = manifest.get("database_dump")
                if dump_member and dump_member in names:
                    scratch.mkdir(parents=True, exist_ok=True)
                    extracted = scratch / Path(dump_member).name
                    with archive.open(dump_member) as src, extracted.open("wb") as out:
                        shutil.copyfileobj(src, out, 1024 * 1024)
                    info = pgtools.inspect(extracted)
                    _check(checks, "dump_readable", info.readable,
                           f"Database dump: {info.table_data} tables with data" if info.readable else (info.problem or "Unreadable"))
                    if info.readable and test_restore:
                        _test_restore(checks, extracted)
                else:
                    _check(checks, "dump_readable", False, "No database dump in the archive")
        except (KeyError, json.JSONDecodeError):
            _check(checks, "manifest_matches", False, "BACKUP_MANIFEST.json is missing or unreadable")
        finally:
            shutil.rmtree(scratch, ignore_errors=True)

    if dump_for_test is not None and test_restore:
        _test_restore(checks, dump_for_test)
    return {"ok": all(c["ok"] for c in checks), "checks": checks, "at": catalog.now_iso()}


def _sha256_member(archive: zipfile.ZipFile, name: str) -> str:
    import hashlib

    digest = hashlib.sha256()
    with archive.open(name) as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def test_database_target() -> pgtools.Target | None:
    url = settings.BACKUP_TEST_DATABASE_URL.strip()
    if not url:
        return None
    test = pgtools.target_from_url(url)
    portal = pgtools.portal_target()
    if (test.host, test.port, test.database) == (portal.host, portal.port, portal.database):
        raise pgtools.PgToolError("BACKUP_TEST_DATABASE_URL points at the portal's own database - refused.")
    return test


def _test_restore(checks: list, dump_file: Path) -> None:
    """Restore into the separate test database and count what arrived."""
    try:
        target = test_database_target()
        if target is None:
            _check(checks, "test_restore", False, "No test database is configured (BACKUP_TEST_DATABASE_URL).")
            return
        pgtools.restore(target, dump_file)
        tables = int(pgtools.scalar(target, "select count(*) from information_schema.tables where table_schema='public'"))
        users = pgtools.scalar(target, "select count(*) from users") if tables else "0"
        _check(checks, "test_restore", tables > 0, f"Restored into the test database: {tables} tables, {users} users")
    except Exception as exc:  # noqa: BLE001
        _check(checks, "test_restore", False, _safe_message(exc))


def _safe_message(exc: Exception) -> str:
    text = str(exc) or type(exc).__name__
    # Belt and braces: never echo a URL with credentials.
    for secret in (pgtools.portal_target().password, settings.DATABASE_URL):
        if secret:
            text = text.replace(secret, "***")
    return text[:500]
