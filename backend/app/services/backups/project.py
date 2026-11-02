"""The "with code" backup: the project tree, the database and a manifest, in
one ZIP that rebuilds the project with its folder structure intact.

What goes in is an allow-list (the folders that ARE the project); what stays
out is a deny-list checked on every file, so a secret dropped into an allowed
folder later is still left out.
"""
from __future__ import annotations

import fnmatch
import hashlib
import os
import stat
import zipfile
from pathlib import Path, PurePosixPath

from app.services.backups.storage import REPO_ROOT

INCLUDE_DIRS = ("backend", "frontend", "deploy", "docs", ".github")
INCLUDE_FILES = (
    "README.md", "CONTEXT.md", ".env.example", ".gitignore", ".gitattributes",
    "pyrightconfig.json", "BUSINESS_RULES_CHANGE_AUDIT.md", "GOOGLE_FORM_FEEDBACK_SYNC_PLAN.md",
)
#: Any path segment matching one of these is skipped (folders and files).
EXCLUDE_SEGMENTS = (
    "node_modules", ".venv", "venv", "__pycache__", ".pytest_cache", ".mypy_cache",
    ".ruff_cache", ".next", "out", "out-static", ".turbo", "coverage", "htmlcov",
    ".git", ".idea", ".vscode",
)
#: Top-level-only folders that hold company data or runtime output.
EXCLUDE_TOP = ("data", "logs")
#: File names that are, or may hold, secrets or bulky runtime output.
EXCLUDE_FILES = (
    ".env", ".env.*", "*.env", "*.pem", "*.key", "*.p12", "*.pfx", "*.dpapi",
    "*.dump", "*.backup", "*.backup-*", "*.db.backup*", "*-shm", "*-wal",
    "*.sql.gz", "*.zip", "*.7z", "*.rar", "*.log", "*.sqlite", "*.sqlite3", "*.db",
    "*.xlsx", "*.xlsm", "*.xls", "*.csv", "*.pyc", "*.tmp", "*.tsbuildinfo",
    "id_rsa*", "*credentials.json", "client_secret*", "*.secret",
    "*passwords*.json", "*passwords*.txt", "*passwords*.csv",
    ".DS_Store", "Thumbs.db",
)
#: Test fixtures the test suite needs (already public in the repository).
ALLOW_PATHS = ("backend/tests/fixtures/*",)
#: Templates that look like env files but hold only blanks - wanted.
ALLOW_FILES = ("*.env.example", ".env.example", "*.example")

MAX_ENTRIES = 50_000
MAX_TOTAL_UNCOMPRESSED = 8 * 1024**3
MAX_RATIO = 200


def _excluded(rel: PurePosixPath) -> bool:
    if any(part in EXCLUDE_SEGMENTS for part in rel.parts[:-1]):
        return True
    if any(fnmatch.fnmatch(str(rel), pattern) for pattern in ALLOW_PATHS):
        return False
    name = rel.name
    if any(fnmatch.fnmatch(name, pattern) for pattern in ALLOW_FILES):
        return False
    return any(fnmatch.fnmatch(name.lower(), pattern) for pattern in EXCLUDE_FILES)


def project_files(root: Path = REPO_ROOT) -> list[tuple[Path, str]]:
    """(absolute path, archive name) for every file that belongs in the ZIP."""
    picked: list[tuple[Path, str]] = []
    for name in INCLUDE_FILES:
        path = root / name
        if path.is_file():
            picked.append((path, name))
    for top in INCLUDE_DIRS:
        base = root / top
        if not base.is_dir():
            continue
        for dirpath, dirnames, filenames in os.walk(base, followlinks=False):
            rel_dir = Path(dirpath).relative_to(root)
            dirnames[:] = [
                d for d in dirnames
                if d not in EXCLUDE_SEGMENTS
                and not (rel_dir == Path(top) and d in EXCLUDE_TOP)
                and not Path(dirpath, d).is_symlink()
            ]
            for filename in filenames:
                full = Path(dirpath) / filename
                if full.is_symlink():
                    continue
                rel = PurePosixPath(*rel_dir.parts, filename)
                if not _excluded(rel):
                    picked.append((full, str(rel)))
    picked.sort(key=lambda item: item[1])
    return picked


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


# -------------------------------------------------------- safe extraction
class UnsafeArchive(ValueError):
    """A ZIP that must not be opened. Message is safe to show."""


def check_zip(path: Path) -> list[zipfile.ZipInfo]:
    """Refuse anything that could write outside the extraction folder, hide a
    link, or blow up on extraction. Returns the entries when safe."""
    try:
        archive = zipfile.ZipFile(path)
    except zipfile.BadZipFile:
        raise UnsafeArchive("That is not a valid ZIP file.") from None
    with archive:
        entries = archive.infolist()
        if len(entries) > MAX_ENTRIES:
            raise UnsafeArchive("The ZIP holds too many files.")
        total = 0
        for entry in entries:
            name = entry.filename.replace("\\", "/")
            parts = PurePosixPath(name).parts
            if (
                name.startswith("/")
                or (len(name) > 1 and name[1] == ":")
                or ".." in parts
                or any(part.endswith(":") for part in parts)
            ):
                raise UnsafeArchive(f"Refused: '{entry.filename}' points outside the archive.")
            mode = entry.external_attr >> 16
            if stat.S_ISLNK(mode):
                raise UnsafeArchive(f"Refused: '{entry.filename}' is a link.")
            total += entry.file_size
            if total > MAX_TOTAL_UNCOMPRESSED:
                raise UnsafeArchive("The ZIP would unpack to more than 8 GB.")
            if entry.compress_size and entry.file_size / max(entry.compress_size, 1) > MAX_RATIO and entry.file_size > 50 * 1024**2:
                raise UnsafeArchive(f"Refused: '{entry.filename}' is suspiciously compressed.")
        bad = archive.testzip()
        if bad is not None:
            raise UnsafeArchive(f"The ZIP is damaged at '{bad}'.")
        return entries


def safe_extract(path: Path, dest: Path) -> list[str]:
    """Extract after check_zip; every target is re-checked to sit under dest.
    Nothing extracted is ever executed."""
    entries = check_zip(path)
    dest = dest.resolve()
    dest.mkdir(parents=True, exist_ok=True)
    written: list[str] = []
    with zipfile.ZipFile(path) as archive:
        for entry in entries:
            name = entry.filename.replace("\\", "/")
            target = (dest / name).resolve()
            if target != dest and dest not in target.parents:
                raise UnsafeArchive(f"Refused: '{entry.filename}' points outside the archive.")
            if entry.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(entry) as src, target.open("wb") as out:
                for chunk in iter(lambda: src.read(1024 * 1024), b""):
                    out.write(chunk)
            written.append(name)
    return written
