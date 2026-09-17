"""Keep the hosted portal in step with the SAP Excel workbook on this PC.

The workbook is refreshed on this computer (it lives in OneDrive), but the
portal runs on a server that cannot see this computer's files. This script
bridges the two: whenever the workbook changes, it uploads it to the portal
through the same Super Admin "Upload SAP file" import the web page uses.

The import on the server is idempotent - re-sending an unchanged row creates
nothing - so an extra upload is harmless.

Standard library only: runs with any Python 3.10+, nothing to install.

Configuration: deploy/sync/sap-uploader.env (git-ignored; copy
sap-uploader.env.example). Environment variables with the same names win.

    python deploy/sync/sap_uploader.py            # upload once if changed
    python deploy/sync/sap_uploader.py --force    # upload once regardless
    python deploy/sync/sap_uploader.py --watch    # keep running, check every few minutes
"""
from __future__ import annotations

import argparse
import hashlib
import http.cookies
import json
import os
import shutil
import sys
import tempfile
import time
import urllib.error
import urllib.request
import uuid
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
CONFIG_FILE = HERE / "sap-uploader.env"
STATE_FILE = Path(os.environ.get("LOCALAPPDATA", str(HERE))) / "bde-portal" / "sap-uploader-state.json"
LOG_FILE = STATE_FILE.parent / "sap-uploader.log"

#: A sleeping free Render instance can take close to a minute to wake up.
TIMEOUT_SECONDS = 180
SETTLE_SECONDS = 10


# ------------------------------------------------------------------ helpers
def log(message: str) -> None:
    line = f"{datetime.now():%Y-%m-%d %H:%M:%S} {message}"
    print(line, flush=True)
    try:
        LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        with LOG_FILE.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    except OSError:
        pass


def load_config() -> dict[str, str]:
    config: dict[str, str] = {}
    if CONFIG_FILE.exists():
        for raw in CONFIG_FILE.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            config[key.strip()] = value.strip().strip('"').strip("'")
    for key in ("PORTAL_URL", "PORTAL_USERNAME", "PORTAL_PASSWORD", "SAP_FILE", "CHECK_MINUTES"):
        if os.environ.get(key):
            config[key] = os.environ[key]
    missing = [k for k in ("PORTAL_URL", "PORTAL_USERNAME", "PORTAL_PASSWORD", "SAP_FILE") if not config.get(k)]
    if missing:
        sys.exit(f"Missing in {CONFIG_FILE.name}: {', '.join(missing)}")
    config["PORTAL_URL"] = config["PORTAL_URL"].rstrip("/")
    return config


def load_state() -> dict:
    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def save_state(state: dict) -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, indent=2), encoding="utf-8")


def file_fingerprint(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def stable_copy(path: Path) -> Path:
    """Wait until the file stops changing, then copy it - Excel and OneDrive
    keep it locked or half-written while they save."""
    last = None
    for _ in range(30):
        stat = path.stat()
        current = (stat.st_size, stat.st_mtime)
        if current == last:
            break
        last = current
        time.sleep(SETTLE_SECONDS)
    handle, name = tempfile.mkstemp(prefix="bde_sap_upload_", suffix=path.suffix)
    os.close(handle)
    for attempt in range(5):
        try:
            # copy2, not copyfile: on Windows it uses the system copy, which
            # still works while Excel holds its exclusive lock on the workbook.
            shutil.copy2(path, name)
            return Path(name)
        except PermissionError:
            time.sleep(5 * (attempt + 1))
    Path(name).unlink(missing_ok=True)
    raise RuntimeError("the workbook stayed locked; try again after Excel/OneDrive finish saving")


# ------------------------------------------------------------------ portal
def request(url: str, *, data: bytes | None = None, headers: dict | None = None, method: str = "GET"):
    req = urllib.request.Request(url, data=data, headers=headers or {}, method=method)
    return urllib.request.urlopen(req, timeout=TIMEOUT_SECONDS)


def sign_in(config: dict[str, str]) -> str:
    body = json.dumps(
        {"identifier": config["PORTAL_USERNAME"], "password": config["PORTAL_PASSWORD"]}
    ).encode()
    try:
        with request(
            f"{config['PORTAL_URL']}/api/auth/login",
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        ) as response:
            for header in response.headers.get_all("Set-Cookie") or []:
                cookie = http.cookies.SimpleCookie(header)
                if "bde_session" in cookie:
                    return cookie["bde_session"].value
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"sign-in refused ({exc.code}): check PORTAL_USERNAME / PORTAL_PASSWORD") from None
    raise RuntimeError("sign-in returned no session")


def sign_out(config: dict[str, str], session: str) -> None:
    try:
        request(
            f"{config['PORTAL_URL']}/api/auth/logout",
            data=b"",
            headers={"Authorization": f"Bearer {session}"},
            method="POST",
        ).close()
    except (urllib.error.URLError, OSError):
        pass


def upload(config: dict[str, str], session: str, path: Path, filename: str) -> dict:
    boundary = uuid.uuid4().hex
    content = path.read_bytes()
    body = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="file"; filename="{filename}"\r\n'
        "Content-Type: application/octet-stream\r\n\r\n"
    ).encode() + content + f"\r\n--{boundary}--\r\n".encode()
    try:
        with request(
            f"{config['PORTAL_URL']}/api/admin/sap-import/upload",
            data=body,
            headers={
                "Authorization": f"Bearer {session}",
                "Content-Type": f"multipart/form-data; boundary={boundary}",
            },
            method="POST",
        ) as response:
            return json.loads(response.read() or b"{}")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:500]
        raise RuntimeError(f"upload refused ({exc.code}): {detail}") from None


# -------------------------------------------------------------------- run
def run_once(config: dict[str, str], *, force: bool) -> bool:
    source = Path(config["SAP_FILE"])
    if not source.exists():
        log(f"workbook not found: {source}")
        return False

    state = load_state()
    stat = source.stat()
    if not force and state.get("size_mtime") == [stat.st_size, stat.st_mtime]:
        return True  # untouched since the last successful upload

    # The workbook itself cannot be opened while Excel has it; a copy can.
    copy = stable_copy(source)
    try:
        fingerprint = file_fingerprint(copy)
        stat = source.stat()
        if not force and state.get("fingerprint") == fingerprint:
            state["size_mtime"] = [stat.st_size, stat.st_mtime]
            save_state(state)
            return True  # saved again, but the content is the same
        log(f"workbook changed - uploading {source.name}")
        session = sign_in(config)
        try:
            result = upload(config, session, copy, source.name)
        finally:
            sign_out(config, session)
    finally:
        copy.unlink(missing_ok=True)

    for item in result.get("files", []):
        log(
            "imported: rows={total_rows} customers +{customers_created} ~{customers_updated} "
            "lines +{lines_created} leads +{leads_created} owners changed {owners_changed} "
            "errors {error_count}".format(**{k: item.get(k, 0) for k in (
                "total_rows", "customers_created", "customers_updated", "lines_created",
                "leads_created", "owners_changed", "error_count",
            )})
        )
        if item.get("unmatched_sales_people"):
            log(f"  sales people not found in the portal: {', '.join(item['unmatched_sales_people'])}")
    save_state(
        {
            "fingerprint": fingerprint,
            "size_mtime": [stat.st_size, stat.st_mtime],
            "uploaded_at": datetime.now().isoformat(timespec="seconds"),
        }
    )
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--force", action="store_true", help="upload even if unchanged")
    parser.add_argument("--watch", action="store_true", help="keep running and check periodically")
    args = parser.parse_args()
    config = load_config()

    if not args.watch:
        try:
            return 0 if run_once(config, force=args.force) else 1
        except (RuntimeError, OSError, urllib.error.URLError) as exc:
            log(f"FAILED: {exc}")
            return 1

    minutes = max(1, int(config.get("CHECK_MINUTES") or 5))
    log(f"watching {config['SAP_FILE']} every {minutes} min -> {config['PORTAL_URL']}")
    force = args.force
    while True:
        try:
            run_once(config, force=force)
            force = False
        except (RuntimeError, OSError, urllib.error.URLError) as exc:
            log(f"FAILED (will retry): {exc}")
        time.sleep(minutes * 60)


if __name__ == "__main__":
    sys.exit(main())
