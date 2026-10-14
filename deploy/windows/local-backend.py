"""The local development backend, restarted whenever backend code changes.

Run by the scheduled task from register-local-backend.ps1, under pythonw (no
window). uvicorn's own --reload restarts on Windows by sending Ctrl+C to its
console, which a hidden background task either does not deliver - the change
is seen, the old code keeps running - or delivers to the task's own process
too and the backend dies. This stops and starts the server directly instead.

    pythonw local-backend.py <log file>
"""
import subprocess
import sys
from pathlib import Path

from watchfiles import PythonFilter, watch

BACKEND = Path(__file__).resolve().parents[2] / "backend"
SERVER = [
    str(BACKEND / ".venv" / "Scripts" / "python.exe"),
    "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", "8000",
]


def start(log):
    # From backend\, so it reads backend\.env. No window of its own.
    return subprocess.Popen(
        SERVER, cwd=BACKEND, stdout=log, stderr=subprocess.STDOUT,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )


with open(sys.argv[1], "w", encoding="utf-8", buffering=1) as log:
    server = start(log)
    # A crash (say, a syntax error mid-edit) leaves the server down until the
    # next save, which restarts it - the same as uvicorn --reload.
    for changes in watch(BACKEND / "app", watch_filter=PythonFilter()):
        names = ", ".join(sorted(Path(path).name for _, path in changes))
        log.write(f"--- code changed ({names}); restarting the server\n")
        server.terminate()
        server.wait()
        server = start(log)
