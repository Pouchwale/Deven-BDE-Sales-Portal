"""Keep the hosted backend awake by pinging its own public URL.

Render's free tier stops a web service after 15 minutes without inbound
traffic, and the next request waits about a minute for a cold start - long
enough for a sign-in to fail. This loop GETs SELF_PUBLIC_URL every
KEEPALIVE_INTERVAL_SECONDS so the backend always has recent traffic.

The URL must be the public HTTPS one. A call to localhost never reaches
Render's routing layer, so it would not reset the idle timer; such URLs are
refused.

One asyncio task, started and cancelled by the app's lifespan. Off unless
ENABLE_KEEPALIVE is true and SELF_PUBLIC_URL is set. A failed ping is logged
in one line and never stops the loop or the app.
"""
from __future__ import annotations

import asyncio
import contextlib
from urllib.parse import urlsplit

import httpx

from app.core.config import settings
from app.core.logging import get_logger

log = get_logger("app.keepalive")

TIMEOUT_SECONDS = 10
_LOOPBACK_HOSTS = {"localhost", "127.0.0.1", "::1", "0.0.0.0"}

_task: asyncio.Task[None] | None = None


def target_url() -> str | None:
    """SELF_PUBLIC_URL, with /health added when it has no path. None (and
    one warning) when it is unset or would not go through Render."""
    raw = settings.SELF_PUBLIC_URL.strip().strip("'\"")
    if not raw:
        log.warning("ENABLE_KEEPALIVE is on but SELF_PUBLIC_URL is blank; keepalive not started")
        return None
    parts = urlsplit(raw)
    host = (parts.hostname or "").lower()
    if parts.scheme != "https" or not host or host in _LOOPBACK_HOSTS:
        log.warning(
            "SELF_PUBLIC_URL must be the public https:// URL, not localhost; keepalive not started"
        )
        return None
    if parts.path in ("", "/"):
        raw = raw.rstrip("/") + "/health"
    return raw


async def ping(client: httpx.AsyncClient, url: str) -> bool:
    """One GET. Logs a single line either way; never raises."""
    try:
        response = await client.get(url)
    except Exception as exc:  # noqa: BLE001 - a failed ping must not stop the loop
        log.warning("keepalive ping failed: %s", type(exc).__name__)
        return False
    if response.is_success:
        log.info("keepalive ping ok (%s)", response.status_code)
        return True
    log.warning("keepalive ping failed: HTTP %s", response.status_code)
    return False


async def _loop(url: str, interval: float) -> None:
    async with httpx.AsyncClient(
        timeout=TIMEOUT_SECONDS, headers={"User-Agent": "portal-keepalive"}
    ) as client:
        while True:
            await asyncio.sleep(interval)
            await ping(client, url)


def start() -> asyncio.Task[None] | None:
    """Start the loop if enabled. Idempotent: a running loop is reused, so a
    second startup never adds a second one. Call from the event loop."""
    global _task
    if not settings.ENABLE_KEEPALIVE:
        return None
    if _task is not None and not _task.done():
        return _task
    url = target_url()
    if url is None:
        return None
    interval = max(30, settings.KEEPALIVE_INTERVAL_SECONDS)
    _task = asyncio.create_task(_loop(url, interval), name="keepalive")
    log.info("keepalive started: every %ss", interval)
    return _task


async def stop() -> None:
    """Cancel the loop and wait for it to finish."""
    global _task
    task, _task = _task, None
    if task is None or task.done():
        return
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task
