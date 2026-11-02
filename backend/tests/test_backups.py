"""Back-Up & Restore: access, backups, verification, archive safety, restore.

PostgreSQL's tools are replaced by fakes here (the suite runs on SQLite);
the real pg_dump -> verify -> restore round trip was exercised against a
throwaway PostgreSQL server (see docs/BACKUP_AND_RESTORE.md).
"""
from __future__ import annotations

import io
import json
import stat
import tempfile
import zipfile
from pathlib import Path

import pytest
from sqlalchemy import select

from app.models.system import AuditEvent
from app.services.backups import catalog, engine, jobs, pgtools, restore, scheduler, storage
from app.services.backups.project import UnsafeArchive, check_zip, project_files
from tests.conftest import SEED_PASSWORD, sign_in, super_admin_headers

FAKE_TARGET = pgtools.Target("localhost", 5432, "bde_portal", "u", "secret-pw", None)


@pytest.fixture()
def store(tmp_path, monkeypatch):
    """A writable backup folder the 'no temp folders' rule accepts, and fake
    PostgreSQL tools that write a real-looking custom-format file."""
    monkeypatch.setattr(storage.tempfile, "gettempdir", lambda: str(tmp_path / "elsewhere"))
    root = storage.validate_destination(str(tmp_path / "backups"))

    def fake_dump(target, out_file):
        out_file.parent.mkdir(parents=True, exist_ok=True)
        out_file.write_bytes(b"PGDMP" + b"\x00" * 200 + b"bde_portal fake dump")

    def fake_inspect(dump_file):
        ok = dump_file.read_bytes()[:5] == b"PGDMP"
        return pgtools.DumpInfo(ok, archive_dbname="bde_portal", dumped_from="18.6", table_data=26 if ok else 0,
                                tables=26, indexes=48, constraints=86,
                                problem=None if ok else "Not a PostgreSQL custom-format dump")

    monkeypatch.setattr(pgtools, "portal_target", lambda: FAKE_TARGET)
    monkeypatch.setattr(pgtools, "dump", fake_dump)
    monkeypatch.setattr(pgtools, "inspect", fake_inspect)
    monkeypatch.setattr(pgtools, "tool_major", lambda name="pg_dump": 18)
    monkeypatch.setattr(pgtools, "server_major", lambda target: 18)
    monkeypatch.setattr(pgtools, "server_version", lambda target: "18.6")
    monkeypatch.setattr(engine, "_check_tools", lambda target: "18.6")
    return root


def _use_store(client, root) -> dict:
    headers = super_admin_headers(client)
    response = client.patch("/api/admin/backups/settings", headers=headers, json={"storage_path": str(root)})
    assert response.status_code == 200, response.text
    return headers


def _run(job: dict) -> None:
    jobs.wait(job["id"], timeout=60)


# ------------------------------------------------------------- access
ROUTES = [
    ("get", "/api/admin/backups/overview", None),
    ("get", "/api/admin/backups", None),
    ("get", "/api/admin/backups/activity", None),
    ("post", "/api/admin/backups", {"type": "database"}),
    ("patch", "/api/admin/backups/settings", {"max_storage_gb": 5}),
    ("post", "/api/admin/backups/storage/test", {"path": "D:/x"}),
    ("post", "/api/admin/backups/restore/inspect", {"backup_id": "x"}),
    ("post", "/api/admin/backups/restore", {"backup_id": "x", "password": "x", "confirm_database": "x"}),
    ("get", "/api/admin/backups/00000000-0000-0000-0000-000000000000/download", None),
]


@pytest.mark.parametrize("name", ["Shail Patel", "Navya Rupawat", "Parth Fulvani"])
def test_only_the_super_admin_reaches_any_backup_route(client, users, name) -> None:
    headers = sign_in(client, users[name])
    for method, path, body in ROUTES:
        response = getattr(client, method)(path, headers=headers, **({"json": body} if body else {}))
        assert response.status_code == 403, (name, path, response.status_code)


def test_signed_out_is_refused(client) -> None:
    for method, path, body in ROUTES:
        response = getattr(client, method)(path, **({"json": body} if body else {}))
        assert response.status_code == 401, path


# ------------------------------------------------------------- storage
def test_storage_refuses_relative_temp_and_project_folders(tmp_path) -> None:
    for bad in ("backups", tempfile.gettempdir(), str(storage.REPO_ROOT / "backend")):
        with pytest.raises(storage.StorageError):
            storage.validate_destination(bad)


def test_storage_creates_the_four_folders(store) -> None:
    assert sorted(p.name for p in store.iterdir()) == sorted(storage.SUBFOLDERS)


def test_no_storage_means_no_backup(client, users, monkeypatch) -> None:
    monkeypatch.setattr(pgtools, "portal_target", lambda: FAKE_TARGET)
    response = client.post("/api/admin/backups", headers=super_admin_headers(client), json={"type": "database"})
    assert response.status_code == 409
    assert "No backup folder" in response.json()["error"]["message"]


# -------------------------------------------------------------- backups
def test_database_backup_is_verified_with_checksum_and_manifest(store) -> None:
    record = engine.create_database_backup(store, actor={"id": None, "name": "t"})
    assert record["status"] == "VERIFIED", record.get("error")
    path = catalog.file_path(store, record)
    assert path.name.startswith("GP3_Database_") and path.suffix == ".dump"
    assert record["sha256"] == engine.sha256_file(path)
    sidecar = json.loads(path.with_suffix(".manifest.json").read_text())
    assert sidecar["sha256"] == record["sha256"] and sidecar["backup_type"] == "DATABASE"
    assert "secret-pw" not in json.dumps(record) + json.dumps(sidecar)


def test_full_backup_keeps_the_folder_structure_and_no_secrets(store) -> None:
    record = engine.create_full_backup(store, actor={"id": None, "name": "t"})
    assert record["status"] == "VERIFIED", record.get("error")
    path = catalog.file_path(store, record)
    assert path.name.startswith("GP3_Full_Backup_") and path.suffix == ".zip"
    with zipfile.ZipFile(path) as archive:
        names = set(archive.namelist())
        manifest = json.loads(archive.read("BACKUP_MANIFEST.json"))
    for top in ("backend/", "frontend/", "deploy/", "docs/", "database/"):
        assert any(n.startswith(top) for n in names), top
    assert {"README.md", ".env.example", "BACKUP_MANIFEST.json"} <= names
    assert "backend/app/main.py" in names and "frontend/package.json" in names
    assert not any(n.endswith("/.env") or n == ".env" or "node_modules/" in n or ".venv/" in n for n in names)
    assert manifest["database_dump"].startswith("database/GP3_Database_")
    assert manifest["file_count"] == len(manifest["files"]) == len(names) - 1
    assert all(len(f["sha256"]) == 64 for f in manifest["files"])


def test_project_selection_skips_secrets_and_build_output() -> None:
    names = [name for _, name in project_files()]
    assert names and not any(
        n.endswith(("/.env", ".dpapi", ".dump", ".zip", ".pyc")) or "/node_modules/" in n or "/.venv/" in n
        for n in names
    )


def test_a_tampered_backup_fails_verification(store) -> None:
    record = engine.create_database_backup(store, actor=None)
    path = catalog.file_path(store, record)
    path.write_bytes(path.read_bytes() + b"tampered")
    result = engine.verify(store, record)
    assert not result["ok"]
    assert any(c["name"] == "sha256" and not c["ok"] for c in result["checks"])


def test_a_backup_is_not_verified_just_because_the_dump_ran(store, monkeypatch) -> None:
    monkeypatch.setattr(pgtools, "dump", lambda target, out: out.write_bytes(b"not a dump"))
    record = engine.create_database_backup(store, actor=None)
    assert record["status"] == "FAILED"


def test_api_backup_job_records_audit_and_history(client, users, store) -> None:
    headers = _use_store(client, store)
    job = client.post("/api/admin/backups", headers=headers, json={"type": "database"}).json()
    _run(job)
    finished = client.get(f"/api/admin/backups/jobs/{job['id']}", headers=headers).json()
    assert finished["status"] == "SUCCEEDED", finished
    history = client.get("/api/admin/backups", headers=headers).json()
    assert history and history[0]["status"] == "VERIFIED"
    actions = [a["action"] for a in client.get("/api/admin/backups/activity", headers=headers).json()]
    assert "BACKUP_CREATED" in actions


# ------------------------------------------------------- archive safety
def _zip_with(tmp_path, name: str, *, symlink: bool = False) -> Path:
    path = tmp_path / "evil.zip"
    with zipfile.ZipFile(path, "w") as archive:
        info = zipfile.ZipInfo(name)
        if symlink:
            info.external_attr = (stat.S_IFLNK | 0o777) << 16
        archive.writestr(info, "x")
    return path


@pytest.mark.parametrize("name", ["../evil.txt", "/etc/passwd", "C:/Windows/evil.txt", "a/../../evil.txt"])
def test_path_traversal_is_refused(tmp_path, name) -> None:
    with pytest.raises(UnsafeArchive):
        check_zip(_zip_with(tmp_path, name))


def test_links_inside_a_zip_are_refused(tmp_path) -> None:
    with pytest.raises(UnsafeArchive):
        check_zip(_zip_with(tmp_path, "link", symlink=True))


def test_a_non_zip_is_refused(tmp_path) -> None:
    path = tmp_path / "x.zip"
    path.write_bytes(b"not a zip")
    with pytest.raises(UnsafeArchive):
        check_zip(path)


def test_an_invalid_dump_is_rejected(tmp_path) -> None:
    path = tmp_path / "x.dump"
    path.write_bytes(b"SELECT 1;")
    from app.services.backups.pgtools import inspect as real_inspect

    info = real_inspect(path)
    assert not info.readable and "custom-format" in info.problem


def test_upload_size_limit_and_type(store) -> None:
    with pytest.raises(restore.RestoreRefused):
        restore.stage_upload(store, "big.dump", iter([b"P" * 600, b"P" * 600]), max_bytes=1000)
    with pytest.raises(restore.RestoreRefused):
        restore.stage_upload(store, "script.sh", iter([b"rm -rf /"]), max_bytes=1000)
    assert not any((store / "restore-staging").glob("upload-*/upload.dump"))


# ------------------------------------------------------------- deletion
def test_the_only_verified_backup_cannot_be_deleted(client, users, store) -> None:
    headers = _use_store(client, store)
    record = engine.create_database_backup(store, actor=None)
    wrong = client.request("DELETE", f"/api/admin/backups/{record['id']}", headers=headers, json={"password": "nope"})
    assert wrong.status_code == 401
    only = client.request("DELETE", f"/api/admin/backups/{record['id']}", headers=headers, json={"password": SEED_PASSWORD})
    assert only.status_code == 409


def test_delete_keeps_the_record_and_audits(client, users, db, store) -> None:
    headers = _use_store(client, store)
    first = engine.create_database_backup(store, actor=None)
    engine.create_database_backup(store, actor=None)
    response = client.request("DELETE", f"/api/admin/backups/{first['id']}", headers=headers, json={"password": SEED_PASSWORD})
    assert response.status_code == 200, response.text
    assert not catalog.file_path(store, first).exists()
    assert catalog.get(store, first["id"])["deleted_at"]
    assert db.execute(select(AuditEvent).where(AuditEvent.action == "BACKUP_DELETED")).first() is not None


# -------------------------------------------------------------- restore
def test_restore_needs_password_and_the_right_database_name(client, users, store) -> None:
    headers = _use_store(client, store)
    record = engine.create_database_backup(store, actor=None)
    base = {"backup_id": record["id"], "confirm_database": "bde_portal", "password": SEED_PASSWORD}
    assert client.post("/api/admin/backups/restore", headers=headers, json={**base, "password": "x"}).status_code == 401
    wrong_db = client.post("/api/admin/backups/restore", headers=headers, json={**base, "confirm_database": "production"})
    assert wrong_db.status_code == 422


def test_another_databases_dump_needs_the_extra_confirmation(client, users, store, monkeypatch) -> None:
    headers = _use_store(client, store)
    record = engine.create_database_backup(store, actor=None)
    monkeypatch.setattr(pgtools, "inspect", lambda f: pgtools.DumpInfo(True, archive_dbname="other_db", table_data=5, tables=5))
    response = client.post("/api/admin/backups/restore", headers=headers, json={
        "backup_id": record["id"], "confirm_database": "bde_portal", "password": SEED_PASSWORD})
    assert response.status_code == 409 and "other_db" in response.json()["error"]["message"]


def test_restore_takes_a_safety_backup_and_rolls_back_a_failed_check(store, monkeypatch) -> None:
    record = engine.create_database_backup(store, actor=None)
    restored = []
    monkeypatch.setattr(pgtools, "restore", lambda target, dump: restored.append(Path(dump).name))
    monkeypatch.setattr(restore, "_snapshot_backup_settings", lambda: {})
    monkeypatch.setattr(restore, "_verify_restored", lambda t, i: (False, "forced"))
    result = restore.run_restore(store, {"backup_id": record["id"]}, actor=None,
                                 allow_other_source=False, progress=lambda *_: None)
    assert result["ok"] is False and result["rolled_back"]
    safety = catalog.get(store, result["safety_backup_id"])
    assert safety["source"] == "pre-restore" and safety["status"] == "VERIFIED"
    # First the chosen backup, then the safety backup put back.
    assert restored == [Path(record["file"]).name, Path(safety["file"]).name]


def test_a_failed_pg_restore_leaves_the_database_unchanged(store, monkeypatch) -> None:
    record = engine.create_database_backup(store, actor=None)

    def boom(target, dump):
        raise pgtools.PgToolError("pg_restore failed (exit 1): lock timeout")

    monkeypatch.setattr(pgtools, "restore", boom)
    monkeypatch.setattr(restore, "_snapshot_backup_settings", lambda: {})
    result = restore.run_restore(store, {"backup_id": record["id"]}, actor=None,
                                 allow_other_source=False, progress=lambda *_: None)
    assert result["ok"] is False and "unchanged" in result["error"]


def test_no_restore_without_a_verified_safety_backup(store, monkeypatch) -> None:
    record = engine.create_database_backup(store, actor=None)
    monkeypatch.setattr(pgtools, "dump", lambda target, out: out.write_bytes(b"broken"))
    touched = []
    monkeypatch.setattr(pgtools, "restore", lambda target, dump: touched.append(dump))
    with pytest.raises(restore.RestoreRefused):
        restore.run_restore(store, {"backup_id": record["id"]}, actor=None,
                            allow_other_source=False, progress=lambda *_: None)
    assert touched == []


# ------------------------------------------------------------ retention
def test_retention_never_removes_the_newest_verified_backup(store) -> None:
    records = [engine.create_database_backup(store, actor=None) for _ in range(3)]
    for r in records:
        r["source"] = "scheduled"
        catalog.save(store, r)
    removed = scheduler.prune(store, keep_daily=0, keep_weekly=0, keep_monthly=0)
    newest = catalog.all_records(store)[0]
    assert newest["id"] not in removed and catalog.file_path(store, newest).exists()


def test_schedule_runs_once_per_day_after_its_time() -> None:
    from datetime import datetime

    assert scheduler.due(datetime(2026, 11, 2, 3, 0), "02:30", None)
    assert not scheduler.due(datetime(2026, 11, 2, 2, 0), "02:30", None)
    assert not scheduler.due(datetime(2026, 11, 2, 3, 0), "02:30", "2026-11-02")
