"""The local development portal: both servers, started at Windows sign-in.

Run by the scheduled task from register-local-backend.ps1, under pythonw (no
window). It starts and keeps running:

* the backend on http://127.0.0.1:8000 (pages and /api), restarted whenever
  backend code changes. uvicorn's own --reload restarts on Windows by sending
  Ctrl+C to its console, which a hidden background task either does not
  deliver - the change is seen, the old code keeps running - or delivers to
  the task's own process too and the backend dies. This stops and starts the
  server directly instead.
* the website's development server on http://localhost:3000 (`next dev`),
  which shows page changes as soon as they are saved and passes /api on to
  the backend. Started by hand it was gone after every restart, so
  localhost:3000 said "refused to connect". Started again if it stops.

    pythonw local-backend.py <backend log> [<frontend log>]
"""
import shutil
import subprocess
import sys
import time
from pathlib import Path

from watchfiles import PythonFilter, watch

ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / "backend"
FRONTEND = ROOT / "frontend"
SERVER = [
    str(BACKEND / ".venv" / "Scripts" / "python.exe"),
    "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", "8000",
]
NEXT = FRONTEND / "node_modules" / "next" / "dist" / "bin" / "next"
# A website server that keeps failing (say, port 3000 taken) is retried at
# most this often, not in a tight loop.
FRONTEND_RETRY_SECONDS = 60


def start(log):
    # From backend\, so it reads backend\.env. No window of its own.
    return subprocess.Popen(
        SERVER, cwd=BACKEND, stdout=log, stderr=subprocess.STDOUT,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )


def start_frontend(log):
    # node directly, not npm.cmd: stopping it then stops the server itself.
    node = shutil.which("node")
    if node is None or not NEXT.exists():
        log.write("--- website server not started: Node.js or frontend\\node_modules "
                  "is missing (run `npm install` in frontend)\n")
        return None
    return subprocess.Popen(
        [node, str(NEXT), "dev", "--port", "3000"], cwd=FRONTEND,
        stdout=log, stderr=subprocess.STDOUT,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )


backend_log = Path(sys.argv[1])
frontend_log = Path(sys.argv[2]) if len(sys.argv) > 2 else backend_log.with_name("frontend-local.log")

with open(backend_log, "w", encoding="utf-8", buffering=1) as log, \
        open(frontend_log, "w", encoding="utf-8", buffering=1) as flog:
    server = start(log)
    frontend = start_frontend(flog)
    frontend_started = time.monotonic()
    # Wakes on a code change, or every 30 s to check the website server.
    # A backend crash (say, a syntax error mid-edit) leaves it down until the
    # next save, which restarts it - the same as uvicorn --reload.
    for changes in watch(BACKEND / "app", watch_filter=PythonFilter(),
                         rust_timeout=30_000, yield_on_timeout=True):
        if (frontend is not None and frontend.poll() is not None
                and time.monotonic() - frontend_started >= FRONTEND_RETRY_SECONDS):
            flog.write(f"--- website server stopped (exit {frontend.returncode}); starting it again\n")
            frontend = start_frontend(flog)
            frontend_started = time.monotonic()
        if not changes:
            continue
        names = ", ".join(sorted(Path(path).name for _, path in changes))
        log.write(f"--- code changed ({names}); restarting the server\n")
        server.terminate()
        server.wait()
        server = start(log)
