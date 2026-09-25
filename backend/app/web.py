"""Serve the portal's pages from the backend itself.

The frontend is built as plain static files (`npm run export:backend` in
frontend/) into app/web, and every GET that no API route claims is answered
from there. The browser therefore talks to ONE origin and ONE service:

* On Render's free tier a separate frontend service kept going to sleep, and
  Render refused to wake it (429 hibernate-rate-limited) - sign-in failed
  with the backend wide awake. One service can be kept awake all month
  inside the free 750 instance-hours; two cannot.
* Same origin, so the session cookie stays first-party and there is no CORS.

A static export cannot send headers, so the page headers the Next server used
to send (next.config.ts) are sent here. RequestContextMiddleware adds the
rest (nosniff, frame and referrer policy, HSTS) and keeps this CSP.

Nothing here is registered when app/web is missing, so a checkout without the
build still runs the API exactly as before.
"""
from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import quote

from fastapi import FastAPI
from fastapi.responses import FileResponse, RedirectResponse, Response
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.requests import Request
from starlette.routing import Match, Route

WEB_DIR = Path(__file__).resolve().parent / "web"

#: The same policy next.config.ts sends in production: everything from this
#: origin, inline scripts/styles for Next's bootstrap and recharts, no framing.
PAGE_CSP = "; ".join(
    [
        "default-src 'self'",
        "script-src 'self' 'unsafe-inline'",
        "style-src 'self' 'unsafe-inline'",
        "img-src 'self' data: blob:",
        "font-src 'self' data:",
        "connect-src 'self'",
        "media-src 'self'",
        "worker-src 'self' blob:",
        "manifest-src 'self'",
        "object-src 'none'",
        "base-uri 'self'",
        "form-action 'self'",
        "frame-ancestors 'none'",
    ]
)

#: Content-hashed build assets never change under the same name.
_IMMUTABLE = "public, max-age=31536000, immutable"
#: Pages and everything else: always check for a newer deploy.
_REVALIDATE = "no-cache"

#: Every method, so the router never answers 405 for a path only this route
#: matches (Starlette makes a function route GET-only by default).
_ALL_METHODS = ["GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"]

#: The customer page used to live at /customers/<id>; it is now
#: /customers/detail?id=<id> (a static export needs every path at build time).
_OLD_CUSTOMER_PATH = re.compile(r"^customers/([^/]+)/?$")


def _resolve(path: str) -> Path | None:
    """The file for a URL path, the way a static host would find it: the
    path itself, then `<path>.html`, then `<path>/index.html`. Never outside
    WEB_DIR."""
    clean = path.strip("/")
    candidates = (
        [WEB_DIR / "index.html"]
        if not clean
        else [WEB_DIR / clean, WEB_DIR / f"{clean}.html", WEB_DIR / clean / "index.html"]
    )
    segment = _segment_file(clean)
    if segment is not None:
        candidates.append(segment)
    for candidate in candidates:
        try:
            resolved = candidate.resolve()
        except OSError:
            continue
        if resolved.is_file() and resolved.is_relative_to(WEB_DIR):
            return resolved
    return None


def _segment_file(clean: str) -> Path | None:
    """Where the export keeps a route-segment data file the client router asks
    for while navigating. The URL joins the segment path with dots; the export
    nests it in folders:

        dashboard/__next.!KHBvcnRhbCk.dashboard.__PAGE__.txt
        -> dashboard/__next.!KHBvcnRhbCk/dashboard/__PAGE__.txt
    """
    base, _, name = clean.rpartition("/")
    if not (name.startswith("__next.") and name.endswith(".txt")):
        return None
    bits = name[: -len(".txt")].split(".")
    if len(bits) <= 2:
        return None
    return WEB_DIR / base / ".".join(bits[:2]) / Path(*bits[2:]).with_suffix(".txt")


def _file(path: Path, status_code: int = 200) -> FileResponse:
    response = FileResponse(path, status_code=status_code)
    relative = path.relative_to(WEB_DIR).as_posix()
    response.headers["Cache-Control"] = (
        _IMMUTABLE if relative.startswith("_next/static/") else _REVALIDATE
    )
    if path.suffix == ".html":
        response.headers["Content-Security-Policy"] = PAGE_CSP
    return response


def available() -> bool:
    return (WEB_DIR / "index.html").is_file()


def mount(app: FastAPI) -> None:
    """Register the catch-all page route. Call AFTER every API route, so it
    only sees what nothing else claimed.

    A plain Starlette route that accepts every method, not a FastAPI route:
    an unknown /api path must stay a 404 whatever the method (a GET-only
    catch-all would turn `POST /api/removed-endpoint` into a 405), and it is
    not an API endpoint, so the route-by-route session audit
    (tests/test_security_matrix.py) rightly does not see it.
    """
    if not available():
        return

    def page(request: Request) -> Response:
        path: str = request.path_params["path"]
        # An API path nothing handled is an API error (the JSON shape), never a
        # page: 405 when a real endpoint lives there under another method,
        # otherwise 404.
        if path == "api" or path.startswith("api/"):
            for route in app.router.routes:
                if route is not catch_all and route.matches(request.scope)[0] == Match.PARTIAL:
                    raise StarletteHTTPException(status_code=405)
            raise StarletteHTTPException(status_code=404)
        # HEAD too: the app probes pages with HEAD.
        if request.method not in ("GET", "HEAD"):
            raise StarletteHTTPException(status_code=405, headers={"Allow": "GET, HEAD"})

        found = _resolve(path)
        if found is not None:
            return _file(found)

        old = _OLD_CUSTOMER_PATH.match(path)
        if old and old.group(1) != "detail":
            return RedirectResponse(
                f"/customers/detail?id={quote(old.group(1), safe='')}", status_code=308
            )

        not_found = WEB_DIR / "404.html"
        if not_found.is_file():
            return _file(not_found, status_code=404)
        raise StarletteHTTPException(status_code=404)

    catch_all = Route("/{path:path}", page, methods=_ALL_METHODS, include_in_schema=False)
    app.router.routes.append(catch_all)
