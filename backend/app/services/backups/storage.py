"""Where backups are kept, and proof the folder is usable.

The destination is the Super Admin's choice (setting `backup.storage_path`),
falling back to BACKUP_STORAGE_ROOT. There is deliberately NO fallback to a
temporary folder: a backup written somewhere that is wiped at the next
restart is worse than no backup, because it looks like one.

    <root>/
    ├── full-project/       GP3_Full_Backup_*.zip
    ├── database-only/      GP3_Database_*.dump (+ .manifest.json)
    ├── restore-staging/    uploads and safety backups being prepared
    └── backup-logs/        catalog/, jobs/, activity.jsonl
"""
from __future__ import annotations

import os
import re
import shutil
import tempfile
import uuid
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy.orm import Session

from app.core.config import settings
from app.services import runtime_settings

SUBFOLDERS = ("full-project", "database-only", "restore-staging", "backup-logs")
REPO_ROOT = Path(__file__).resolve().parents[4]


class StorageError(ValueError):
    """A destination that cannot hold backups. Message is safe to show."""


@dataclass
class StorageStatus:
    configured: bool
    path: str | None
    available: bool
    problem: str | None
    used_bytes: int = 0
    free_bytes: int | None = None
    total_bytes: int | None = None
    max_bytes: int | None = None


#: "D:\\…" / "C:/…": a folder on a Windows PC.
_WINDOWS_DRIVE = re.compile(r"^[A-Za-z]:[\\/]")


def host() -> dict[str, object]:
    """Where this portal is running, for the page's wording and examples.

    The live portal runs on Render (Linux); the office PC runs Windows. A
    folder path only means something on the machine that runs the portal.
    """
    windows = os.name == "nt"
    render = bool(os.environ.get("RENDER"))
    return {
        "os": "windows" if windows else "linux",
        "render": render,
        "example": "D:\\GP3\\backups" if windows else "/var/data/backups",
    }


def configured_root(db: Session) -> str:
    return str(runtime_settings.get(db, "backup.storage_path") or "").strip()


def validate_destination(raw: str) -> Path:
    """Check a folder before it is accepted. Creates the subfolders and proves
    a file can be written and removed there. Raises StorageError."""
    text = (raw or "").strip()
    if not text:
        raise StorageError("Choose a folder for backups.")
    if os.name != "nt" and _WINDOWS_DRIVE.match(text):
        # The commonest mistake on the live site: a folder on the PC the page
        # is being viewed from. The server cannot reach that PC's drives.
        raise StorageError(
            f"{text[:2]}\\ is a drive on a Windows PC, not on this server. The live portal runs on "
            "Render (Linux): use a folder on its persistent disk, e.g. /var/data/backups."
        )
    path = Path(text).expanduser()
    if not path.is_absolute():
        raise StorageError(f"Use a full folder path, e.g. {host()['example']}.")
    resolved = path.resolve()
    temp_root = Path(tempfile.gettempdir()).resolve()
    if resolved == temp_root or temp_root in resolved.parents:
        raise StorageError("A temporary folder is wiped without warning - choose a permanent one.")
    if resolved == REPO_ROOT or REPO_ROOT in resolved.parents:
        raise StorageError(
            "That folder is inside the project, which is pushed to GitHub. "
            "Backups hold customer data and must live outside it."
        )
    try:
        resolved.mkdir(parents=True, exist_ok=True)
        for name in SUBFOLDERS:
            (resolved / name).mkdir(exist_ok=True)
        probe = resolved / "backup-logs" / f".write-test-{uuid.uuid4().hex}"
        probe.write_bytes(b"ok")
        if probe.read_bytes() != b"ok":
            raise StorageError("The folder did not read back what was written.")
        probe.unlink()
    except StorageError:
        raise
    except OSError as exc:
        raise StorageError(f"Cannot write to that folder ({exc.strerror or type(exc).__name__}).") from None
    return resolved


def root(db: Session) -> Path:
    """The usable storage root, or StorageError saying why not."""
    text = configured_root(db)
    if not text:
        raise StorageError(
            "No backup folder is set. Choose one under Storage before creating backups."
        )
    return validate_destination(text)


def folder(db: Session, name: str) -> Path:
    assert name in SUBFOLDERS
    return root(db) / name


def _folder_size(path: Path) -> int:
    total = 0
    for dirpath, _dirs, files in os.walk(path):
        for name in files:
            try:
                total += (Path(dirpath) / name).stat().st_size
            except OSError:
                continue
    return total


def status(db: Session) -> StorageStatus:
    text = configured_root(db)
    max_gb = float(runtime_settings.get(db, "backup.max_storage_gb") or 0)
    max_bytes = int(max_gb * 1024**3) if max_gb > 0 else None
    if not text:
        return StorageStatus(False, None, False, "No backup folder is set.", max_bytes=max_bytes)
    try:
        path = validate_destination(text)
    except StorageError as exc:
        return StorageStatus(True, text, False, str(exc), max_bytes=max_bytes)
    usage = shutil.disk_usage(path)
    used = sum(_folder_size(path / name) for name in SUBFOLDERS)
    return StorageStatus(
        True, str(path), True, None,
        used_bytes=used, free_bytes=usage.free, total_bytes=usage.total, max_bytes=max_bytes,
    )


def label(path: Path | str | None) -> str:
    """What the UI and the audit trail call a destination: the folder's name
    and its parent, never a full server path."""
    if not path:
        return "not configured"
    p = Path(path)
    return f"…/{p.parent.name}/{p.name}" if p.parent.name else p.name
