"""Migration checksums must not depend on line endings.

Git stores the .sql files with LF; a Windows checkout may have CRLF. A
migration applied from one must still verify from the other, or a database
migrated on a developer's PC refuses to start on a Linux host.
"""
from __future__ import annotations

import hashlib
from pathlib import Path

from app.db import migrate


def _write(tmp_path: Path, body: bytes) -> Path:
    path = tmp_path / "0099_example.sql"
    path.write_bytes(body)
    return path


def test_checksum_is_the_same_for_lf_and_crlf(tmp_path) -> None:
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    lf = _write(tmp_path / "a", b"ALTER TABLE x ADD COLUMN y INTEGER;\n-- note\n")
    crlf = _write(tmp_path / "b", b"ALTER TABLE x ADD COLUMN y INTEGER;\r\n-- note\r\n")
    assert migrate.checksum(lf) == migrate.checksum(crlf)


def test_a_checksum_stored_by_the_old_runner_still_matches(tmp_path) -> None:
    body_lf = b"CREATE TABLE t (id INTEGER);\n"
    path = _write(tmp_path, body_lf)
    old_lf = hashlib.sha256(body_lf).hexdigest()
    old_crlf = hashlib.sha256(body_lf.replace(b"\n", b"\r\n")).hexdigest()
    assert migrate.matches(old_lf, path)
    assert migrate.matches(old_crlf, path)


def test_a_real_edit_is_still_refused(tmp_path) -> None:
    path = _write(tmp_path, b"CREATE TABLE t (id INTEGER);\n")
    stored = migrate.checksum(path)
    path.write_bytes(b"CREATE TABLE t (id INTEGER, extra TEXT);\n")
    assert not migrate.matches(stored, path)
