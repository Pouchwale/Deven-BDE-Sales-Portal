"""What backups exist, kept ON THE BACKUP DISK.

Why not a database table: a restore replaces the database. A catalog stored
there would forget the backup that was just restored, rewind to whatever list
the dump happened to contain, and lose the record of the restore itself. Kept
beside the files, the history survives any restore.

    backup-logs/catalog/<id>.json   one record per backup (kept after the file
                                    is deleted or moved, marked as such)
    backup-logs/activity.jsonl      every action, one line each
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

KINDS = ("DATABASE", "FULL")
SOURCES = ("manual", "scheduled", "pre-restore", "uploaded")


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _catalog_dir(root: Path) -> Path:
    path = root / "backup-logs" / "catalog"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _write(path: Path, data: dict[str, Any]) -> None:
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")
    tmp.replace(path)


def new_record(root: Path, *, kind: str, source: str, created_by: dict[str, Any] | None) -> dict[str, Any]:
    assert kind in KINDS and source in SOURCES
    record = {
        "id": str(uuid.uuid4()),
        "kind": kind,
        "source": source,
        "status": "RUNNING",
        "file": None,  # relative to the storage root
        "size_bytes": None,
        "sha256": None,
        "created_at": now_iso(),
        "finished_at": None,
        "created_by": created_by,
        "verification": None,
        "verified_at": None,
        "error": None,
        "git_commit": None,
        "pg_version": None,
        "database": None,
        "deleted_at": None,
    }
    save(root, record)
    return record


def save(root: Path, record: dict[str, Any]) -> None:
    _write(_catalog_dir(root) / f"{record['id']}.json", record)


def get(root: Path, backup_id: str) -> dict[str, Any] | None:
    try:
        uuid.UUID(backup_id)
    except ValueError:
        return None
    path = _catalog_dir(root) / f"{backup_id}.json"
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def file_path(root: Path, record: dict[str, Any]) -> Path | None:
    """The archive on disk, refusing anything that would point outside root."""
    rel = record.get("file")
    if not rel:
        return None
    candidate = (root / rel).resolve()
    if root.resolve() not in candidate.parents:
        return None
    return candidate


def all_records(root: Path) -> list[dict[str, Any]]:
    records = []
    for path in _catalog_dir(root).glob("*.json"):
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        located = file_path(root, record)
        record["file_present"] = bool(located and located.exists())
        records.append(record)
    records.sort(key=lambda r: r.get("created_at") or "", reverse=True)
    return records


# --------------------------------------------------------------- activity
def log(root: Path, action: str, *, actor: dict[str, Any] | None = None, **details: Any) -> None:
    line = {"at": now_iso(), "action": action, "actor": actor, **details}
    path = root / "backup-logs" / "activity.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(line, default=str) + "\n")


def activity(root: Path, limit: int = 100) -> list[dict[str, Any]]:
    path = root / "backup-logs" / "activity.jsonl"
    if not path.exists():
        return []
    lines = path.read_text(encoding="utf-8").splitlines()[-limit:]
    out = []
    for line in reversed(lines):
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out
