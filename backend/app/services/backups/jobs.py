"""Long backup/restore work runs in a background thread, never inside the
HTTP request. Progress is written to backup-logs/jobs/<id>.json, so a page
refresh (or a different device) can pick the job up again, and a job cut off
by a restart shows as INTERRUPTED rather than spinning forever.

One job at a time: two dumps or a dump during a restore would fight over the
same database and the same disk.
"""
from __future__ import annotations

import json
import threading
import uuid
from pathlib import Path
from typing import Any, Callable

from app.core.logging import get_logger
from app.services.backups import catalog

log = get_logger("app.backups")
_lock = threading.Lock()
_running: dict[str, threading.Thread] = {}

ACTIVE = ("QUEUED", "RUNNING")


class Busy(RuntimeError):
    """Another backup or restore is already running."""


def _dir(root: Path) -> Path:
    path = root / "backup-logs" / "jobs"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _save(root: Path, job: dict[str, Any]) -> None:
    path = _dir(root) / f"{job['id']}.json"
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(job, indent=2, default=str), encoding="utf-8")
    tmp.replace(path)


def get(root: Path, job_id: str) -> dict[str, Any] | None:
    try:
        uuid.UUID(job_id)
    except ValueError:
        return None
    path = _dir(root) / f"{job_id}.json"
    if not path.exists():
        return None
    job = json.loads(path.read_text(encoding="utf-8"))
    if job["status"] in ACTIVE and job_id not in _running:
        # Written by a process that is gone (restart, deploy, crash).
        job.update(status="INTERRUPTED", step="Stopped by a server restart", finished_at=catalog.now_iso())
        _save(root, job)
    return job


def recent(root: Path, limit: int = 20) -> list[dict[str, Any]]:
    jobs = []
    for path in sorted(_dir(root).glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)[:limit]:
        job = get(root, path.stem)
        if job:
            jobs.append(job)
    return jobs


def active(root: Path) -> dict[str, Any] | None:
    for job_id in list(_running):
        job = get(root, job_id)
        if job and job["status"] in ACTIVE:
            return job
    return None


def start(
    root: Path,
    *,
    kind: str,
    title: str,
    actor: dict | None,
    work: Callable[[Callable[[str, int], None]], dict[str, Any]],
    on_done: Callable[[dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    """Queue `work(progress)` on a background thread. `work` returns a result
    dict; {"ok": False, "error": ...} marks the job FAILED."""
    with _lock:
        if any(t.is_alive() for t in _running.values()):
            raise Busy("Another backup or restore is running. Wait for it to finish.")
        job = {
            "id": str(uuid.uuid4()), "kind": kind, "title": title, "status": "QUEUED",
            "step": "Queued", "percent": 0, "actor": actor, "created_at": catalog.now_iso(),
            "started_at": None, "finished_at": None, "result": None, "error": None,
        }
        _save(root, job)

        def progress(step: str, percent: int) -> None:
            job.update(step=step, percent=max(job["percent"], min(int(percent), 99)))
            _save(root, job)

        def runner() -> None:
            job.update(status="RUNNING", started_at=catalog.now_iso())
            _save(root, job)
            try:
                result = work(progress)
                ok = bool(result.get("ok", True))
                job.update(
                    status="SUCCEEDED" if ok else "FAILED", result=result,
                    error=None if ok else result.get("error"),
                    step="Completed" if ok else "Failed", percent=100 if ok else job["percent"],
                )
            except Exception as exc:  # noqa: BLE001 - recorded on the job
                log.exception("Backup job %s failed", job["id"])
                job.update(status="FAILED", error=str(exc)[:500] or type(exc).__name__, step="Failed")
            job["finished_at"] = catalog.now_iso()
            _save(root, job)
            if on_done:
                try:
                    on_done(job)
                except Exception:  # noqa: BLE001
                    log.exception("Backup job %s follow-up failed", job["id"])
            _running.pop(job["id"], None)

        thread = threading.Thread(target=runner, name=f"backup-{job['id'][:8]}", daemon=True)
        _running[job["id"]] = thread
        thread.start()
        return job


def wait(job_id: str, timeout: float | None = None) -> None:
    """Tests and the scheduler: block until a job's thread ends."""
    thread = _running.get(job_id)
    if thread:
        thread.join(timeout)
