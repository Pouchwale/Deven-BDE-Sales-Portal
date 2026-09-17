"""Request context, access logging and security headers - one pure ASGI layer.

Pure ASGI rather than Starlette's BaseHTTPMiddleware on purpose: that class
buffers through an anyio memory stream and is known to interfere with
streaming responses and background tasks. The chat endpoint streams
Server-Sent Events, so this only ever touches the `http.response.start`
message (headers) and passes every body chunk straight through.

What it does, per HTTP request
  * request id: accepts an inbound X-Request-ID only if it looks like an id
    (letters, digits, `-_.`, up to 64 chars), otherwise generates one; echoes
    it on the response and exposes it to logs through a context variable.
  * one access log line: method, ROUTE TEMPLATE (never the raw path or query
    string, which can carry emails and tokens), status, duration, user id
    (from request.state.user_id, if the auth dependency set it), client ip.
    No headers and no bodies are ever logged.
  * security headers on every response.
  * an unhandled exception becomes the standard 500 envelope, with the full
    traceback logged server-side under the request id.
"""
from __future__ import annotations

import re
import time
import uuid
from collections.abc import Awaitable, Callable, MutableMapping
from typing import Any

from starlette.datastructures import MutableHeaders

from app.core.logging import get_logger, request_id_var

Scope = MutableMapping[str, Any]
Message = MutableMapping[str, Any]
Receive = Callable[[], Awaitable[Message]]
Send = Callable[[Message], Awaitable[None]]
ASGIApp = Callable[[Scope, Receive, Send], Awaitable[None]]

REQUEST_ID_HEADER = "X-Request-ID"
_SAFE_REQUEST_ID = re.compile(r"^[A-Za-z0-9._-]{8,64}$")

_access_log = get_logger("app.access")
_error_log = get_logger("app.errors")

#: The interactive docs pages load scripts and styles from a CDN, so the
#: strict API policy cannot apply to them. Docs only exist outside production.
_DOCS_PATHS = ("/docs", "/redoc", "/openapi.json")


def _accept_request_id(value: str | None) -> str:
    if value and _SAFE_REQUEST_ID.match(value):
        return value
    return uuid.uuid4().hex


def _route_template(scope: Scope) -> str:
    """The matched route's path template, e.g. /api/leads/{lead_id}.

    Starlette writes the matched endpoint into the (shared) scope while
    routing; the template is looked up from the app's routes by endpoint.
    """
    app = scope.get("app")
    endpoint = scope.get("endpoint")
    if app is None or endpoint is None:
        return "<unmatched>"
    cache: dict[Any, str] | None = getattr(app.state, "_route_templates", None)
    if cache is None:
        cache = {}
        for route in getattr(app, "routes", []):
            route_endpoint = getattr(route, "endpoint", None)
            path = getattr(route, "path_format", None) or getattr(route, "path", None)
            if route_endpoint is not None and path:
                cache.setdefault(route_endpoint, path)
        app.state._route_templates = cache
    return cache.get(endpoint, "<unmatched>")


def _client_ip(scope: Scope) -> str | None:
    """The socket peer. Deliberately NOT X-Forwarded-For: the access log
    records who connected; the trusted-proxy aware address used for rate
    limiting and audit lives in the auth layer, which may set
    request.state.client_ip - preferred when present."""
    state = scope.get("state") or {}
    ip = state.get("client_ip") if isinstance(state, dict) else None
    if ip:
        return str(ip)
    client = scope.get("client")
    return client[0] if client else None


def _user_id(state: Any) -> Any:
    """request.state.user_id if set, else the authenticated session row's
    user_id (app.core.deps stores the row as request.state.session)."""
    if not isinstance(state, dict):
        return None
    if state.get("user_id"):
        return state["user_id"]
    session_row = state.get("session")
    if session_row is None:
        return None
    try:
        return getattr(session_row, "user_id", None)
    except Exception:  # noqa: BLE001 - a detached ORM row must not break logging
        return None


class RequestContextMiddleware:
    def __init__(
        self,
        app: ASGIApp,
        *,
        production: bool = False,
        hsts: bool = False,
        slow_request_ms: int = 1500,
    ) -> None:
        self.app = app
        self.production = production
        self.hsts = hsts
        self.slow_request_ms = slow_request_ms

    def _security_headers(self, headers: MutableHeaders, path: str) -> None:
        headers.setdefault("X-Content-Type-Options", "nosniff")
        headers.setdefault("X-Frame-Options", "DENY")
        headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
        headers.setdefault("Cross-Origin-Opener-Policy", "same-origin")
        is_docs = not self.production and path.startswith(_DOCS_PATHS)
        if not is_docs:
            headers.setdefault(
                "Content-Security-Policy", "default-src 'none'; frame-ancestors 'none'"
            )
        if path.startswith("/api") or path == "/health":
            headers["Cache-Control"] = "no-store"
        if self.hsts:
            headers.setdefault(
                "Strict-Transport-Security", "max-age=31536000; includeSubDomains"
            )

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        inbound = None
        for name, value in scope.get("headers") or ():
            if name == b"x-request-id":
                inbound = value.decode("latin-1")
                break
        request_id = _accept_request_id(inbound)
        scope.setdefault("state", {})
        if isinstance(scope["state"], dict):
            scope["state"]["request_id"] = request_id
        token = request_id_var.set(request_id)

        path: str = scope.get("path", "")
        started = time.perf_counter()
        status_code = 500
        response_started = False

        async def send_wrapper(message: Message) -> None:
            nonlocal status_code, response_started
            if message["type"] == "http.response.start":
                response_started = True
                status_code = int(message["status"])
                headers = MutableHeaders(scope=message)
                headers[REQUEST_ID_HEADER] = request_id
                self._security_headers(headers, path)
            await send(message)

        try:
            try:
                await self.app(scope, receive, send_wrapper)
            except Exception as exc:  # noqa: BLE001 - this IS the last resort
                _error_log.error(
                    "unhandled exception",
                    exc_info=exc,
                    extra={"method": scope.get("method"), "route": _route_template(scope)},
                )
                if response_started:
                    # Mid-stream (e.g. SSE): nothing can be sent any more; let
                    # the server close the connection.
                    raise
                from starlette.responses import JSONResponse

                from app.core.errors import internal_error_body

                response = JSONResponse(internal_error_body(request_id), status_code=500)
                await response(scope, receive, send_wrapper)
        finally:
            duration_ms = round((time.perf_counter() - started) * 1000, 1)
            state = scope.get("state") or {}
            user_id = _user_id(state)
            fields = {
                "method": scope.get("method"),
                "route": _route_template(scope),
                "status": status_code,
                "duration_ms": duration_ms,
                "user_id": str(user_id) if user_id else None,
                "client_ip": _client_ip(scope),
            }
            if duration_ms >= self.slow_request_ms:
                _access_log.warning("slow request", extra=fields)
            else:
                _access_log.info("request", extra=fields)
            request_id_var.reset(token)
