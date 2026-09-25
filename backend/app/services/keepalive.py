"""Keep the hosted portal awake by pinging its own public URLs - for free.

Render's free tier stops a web service after 15 minutes without inbound
traffic, and the next request waits about a minute for a cold start - long
enough for a sign-in to fail. This loop GETs the frontend's /health (which
the frontend proxies to the backend, so both stay awake) and the backend's
own public URL every KEEPALIVE_INTERVAL_SECONDS.

Only inside KEEPALIVE_ACTIVE_HOURS on KEEPALIVE_ACTIVE_DAYS (IST working
hours by default): both services awake round the clock would need ~1,440
instance-hours a month, and Render suspends free services past 750. Outside
the window the loop stays quiet and the services sleep; the GitHub Action in
.github/workflows/wake-portal.yml wakes them each morning.

The URLs must be the public HTTPS ones. A call to localhost never reaches
Render's routing layer, so it would not reset the idle timer; such URLs are
refused.

One asyncio task, started and cancelled by the app's lifespan. On by itself
on Render (never on a pull-request preview, never in local dev); a failed
ping is logged in one line and never stops the loop or the app.
"""
from __future__ import annotations

import asyncio
import contextlib
from datetime import datetime, time, timedelta, timezone
from urllib.parse import urlsplit

import httpx

from app.core.config import settings
from app.core.logging import get_logger

log = get_logger("app.keepalive")

TIMEOUT_SECONDS = 10
_LOOPBACK_HOSTS = {"localhost", "127.0.0.1", "::1", "0.0.0.0"}
_TRUE = {"1", "true", "yes", "on"}
_FALSE = {"0", "false", "no", "off"}
_DAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")

_task: asyncio.Task[None] | None = None


class BadWindow(ValueError):
    pass


def enabled() -> bool:
    """ENABLE_KEEPALIVE true/false wins; otherwise on only on Render, and
    never on a pull-request preview."""
    flag = settings.ENABLE_KEEPALIVE.strip().lower()
    if flag in _TRUE:
        return True
    if flag in _FALSE:
        return False
    on_render = bool(settings.RENDER.strip())
    preview = settings.IS_PULL_REQUEST.strip().lower() == "true"
    return on_render and not preview


def _normalise(raw: str) -> str | None:
    """A public https URL, with /health added when it has no path."""
    raw = raw.strip().strip("'\"")
    parts = urlsplit(raw)
    host = (parts.hostname or "").lower()
    if parts.scheme != "https" or not host or host in _LOOPBACK_HOSTS:
        log.warning("keepalive: ignoring %r - it must be a public https:// URL, not localhost", raw)
        return None
    if parts.path in ("", "/"):
        raw = raw.rstrip("/") + "/health"
    return raw


def target_urls() -> list[str]:
    """SELF_PUBLIC_URL entries, the frontend, and this service's own public
    URL (RENDER_EXTERNAL_URL, set by Render) - de-duplicated, invalid ones
    dropped with a warning."""
    candidates = [
        *settings.SELF_PUBLIC_URL.split(","),
        settings.KEEPALIVE_FRONTEND_URL,
        settings.RENDER_EXTERNAL_URL,
    ]
    urls: list[str] = []
    for candidate in candidates:
        if not candidate.strip():
            continue
        url = _normalise(candidate)
        if url and url not in urls:
            urls.append(url)
    return urls


def _parse_window() -> tuple[set[int], tuple[time, time] | None]:
    """(weekdays, (start, end)) from the settings. Empty days = every day;
    None hours = all day. Raises BadWindow on a malformed value."""
    days: set[int] = set()
    for name in settings.KEEPALIVE_ACTIVE_DAYS.split(","):
        name = name.strip().lower()[:3]
        if not name:
            continue
        if name not in _DAYS:
            raise BadWindow(f"KEEPALIVE_ACTIVE_DAYS has an unknown day {name!r}")
        days.add(_DAYS.index(name))
    hours = settings.KEEPALIVE_ACTIVE_HOURS.strip()
    if not hours:
        return days, None
    try:
        start_text, end_text = hours.split("-")
        start = time.fromisoformat(start_text.strip())
        end = time.fromisoformat(end_text.strip())
    except ValueError:
        raise BadWindow("KEEPALIVE_ACTIVE_HOURS must look like 08:30-20:30") from None
    return days, (start, end)


def in_window(now_utc: datetime) -> bool:
    """Is this moment inside the active window, in local time?"""
    days, hours = _parse_window()
    local = now_utc + timedelta(minutes=settings.KEEPALIVE_UTC_OFFSET_MINUTES)
    if days and local.weekday() not in days:
        return False
    if hours is None:
        return True
    start, end = hours
    now = local.time()
    # A window like 22:00-06:00 runs past midnight.
    return start <= now < end if start <= end else (now >= start or now < end)


async def ping(client: httpx.AsyncClient, url: str) -> bool:
    """One GET. Logs a single line either way; never raises."""
    try:
        response = await client.get(url)
    except Exception as exc:  # noqa: BLE001 - a failed ping must not stop the loop
        log.warning("keepalive ping failed: %s (%s)", type(exc).__name__, urlsplit(url).hostname)
        return False
    if response.is_success:
        log.info("keepalive ping ok (%s, %s)", response.status_code, urlsplit(url).hostname)
        return True
    log.warning("keepalive ping failed: HTTP %s (%s)", response.status_code, urlsplit(url).hostname)
    return False


async def _loop(urls: list[str], interval: float) -> None:
    async with httpx.AsyncClient(
        timeout=TIMEOUT_SECONDS, headers={"User-Agent": "portal-keepalive"}
    ) as client:
        while True:
            await asyncio.sleep(interval)
            if not in_window(datetime.now(timezone.utc)):
                continue
            for url in urls:
                await ping(client, url)


def start() -> asyncio.Task[None] | None:
    """Start the loop if enabled. Idempotent: a running loop is reused, so a
    second startup never adds a second one. Call from the event loop."""
    global _task
    if not enabled():
        return None
    if _task is not None and not _task.done():
        return _task
    urls = target_urls()
    if not urls:
        log.warning("keepalive is on but there is no public URL to ping; not started")
        return None
    try:
        _parse_window()
    except BadWindow as exc:
        log.warning("keepalive not started: %s", exc)
        return None
    interval = max(30, settings.KEEPALIVE_INTERVAL_SECONDS)
    _task = asyncio.create_task(_loop(urls, interval), name="keepalive")
    log.info("keepalive started: %s URL(s) every %ss", len(urls), interval)
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
