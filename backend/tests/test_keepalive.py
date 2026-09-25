"""The self-ping that keeps the Render free-tier portal awake, for free."""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import httpx
import pytest

from app.core.config import settings
from app.services import keepalive

FRONTEND = "https://deven-bde-sales-portal-frontend.onrender.com"


def _configure(monkeypatch, **values) -> None:
    defaults = {
        "ENABLE_KEEPALIVE": "auto",
        "SELF_PUBLIC_URL": "",
        "KEEPALIVE_FRONTEND_URL": FRONTEND,
        "KEEPALIVE_INTERVAL_SECONDS": 600,
        "KEEPALIVE_ACTIVE_HOURS": "08:30-20:30",
        "KEEPALIVE_ACTIVE_DAYS": "mon,tue,wed,thu,fri,sat",
        "KEEPALIVE_UTC_OFFSET_MINUTES": 330,
        "RENDER": "",
        "RENDER_EXTERNAL_URL": "",
        "IS_PULL_REQUEST": "",
    }
    defaults.update(values)
    for key, value in defaults.items():
        monkeypatch.setattr(settings, key, value)


# ------------------------------------------------------------- switched on
def test_off_in_local_dev(monkeypatch) -> None:
    _configure(monkeypatch)
    assert keepalive.enabled() is False


def test_on_by_itself_on_render(monkeypatch) -> None:
    _configure(monkeypatch, RENDER="true")
    assert keepalive.enabled() is True


def test_never_on_a_render_preview(monkeypatch) -> None:
    _configure(monkeypatch, RENDER="true", IS_PULL_REQUEST="true")
    assert keepalive.enabled() is False


@pytest.mark.parametrize(("flag", "expected"), [("true", True), ("false", False), ("off", False)])
def test_explicit_flag_wins(monkeypatch, flag, expected) -> None:
    _configure(monkeypatch, RENDER="true", ENABLE_KEEPALIVE=flag)
    assert keepalive.enabled() is expected


def test_nothing_starts_when_disabled(monkeypatch) -> None:
    _configure(monkeypatch, ENABLE_KEEPALIVE="false")

    async def run():
        return keepalive.start()

    assert asyncio.run(run()) is None


# -------------------------------------------------------------------- URLs
def test_pings_the_frontend_and_this_service(monkeypatch) -> None:
    _configure(
        monkeypatch,
        RENDER="true",
        RENDER_EXTERNAL_URL="https://deven-bde-sales-portal-backend.onrender.com",
    )
    assert keepalive.target_urls() == [
        f"{FRONTEND}/health",
        "https://deven-bde-sales-portal-backend.onrender.com/health",
    ]


def test_duplicates_are_dropped(monkeypatch) -> None:
    _configure(monkeypatch, SELF_PUBLIC_URL=f"{FRONTEND}/health, {FRONTEND}/")
    assert keepalive.target_urls() == [f"{FRONTEND}/health"]


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1:8000/health",
        "https://localhost/health",
        "http://portal.onrender.com/health",
        "portal.onrender.com/health",
    ],
)
def test_loopback_or_plain_http_is_refused(monkeypatch, url) -> None:
    _configure(monkeypatch, SELF_PUBLIC_URL=url, KEEPALIVE_FRONTEND_URL="")
    assert keepalive.target_urls() == []


def test_no_url_means_no_loop(monkeypatch, caplog) -> None:
    _configure(monkeypatch, ENABLE_KEEPALIVE="true", KEEPALIVE_FRONTEND_URL="")

    async def run():
        return keepalive.start()

    assert asyncio.run(run()) is None
    assert "no public URL" in caplog.text


# ------------------------------------------------------------------ window
def _utc(text: str) -> datetime:
    return datetime.fromisoformat(text).replace(tzinfo=timezone.utc)


@pytest.mark.parametrize(
    ("utc", "inside"),
    [
        ("2026-09-25T03:00:00", True),   # Fri 08:30 IST - opens
        ("2026-09-25T02:59:00", False),  # Fri 08:29 IST
        ("2026-09-25T14:59:00", True),   # Fri 20:29 IST
        ("2026-09-25T15:00:00", False),  # Fri 20:30 IST - closed
        ("2026-09-27T06:00:00", False),  # Sun 11:30 IST - day off
        ("2026-09-26T06:00:00", True),   # Sat 11:30 IST
    ],
)
def test_ist_working_hours(monkeypatch, utc, inside) -> None:
    _configure(monkeypatch)
    assert keepalive.in_window(_utc(utc)) is inside


def test_blank_window_means_always(monkeypatch) -> None:
    _configure(monkeypatch, KEEPALIVE_ACTIVE_HOURS="", KEEPALIVE_ACTIVE_DAYS="")
    assert keepalive.in_window(_utc("2026-09-27T20:00:00")) is True


def test_window_past_midnight(monkeypatch) -> None:
    _configure(monkeypatch, KEEPALIVE_ACTIVE_HOURS="22:00-06:00", KEEPALIVE_ACTIVE_DAYS="")
    assert keepalive.in_window(_utc("2026-09-25T18:00:00")) is True   # 23:30 IST
    assert keepalive.in_window(_utc("2026-09-25T06:00:00")) is False  # 11:30 IST


def test_malformed_window_does_not_start(monkeypatch, caplog) -> None:
    _configure(monkeypatch, ENABLE_KEEPALIVE="true", KEEPALIVE_ACTIVE_HOURS="morning")

    async def run():
        return keepalive.start()

    assert asyncio.run(run()) is None
    assert "08:30-20:30" in caplog.text


# -------------------------------------------------------------------- loop
def test_one_loop_only_and_cancelled_cleanly(monkeypatch) -> None:
    _configure(monkeypatch, ENABLE_KEEPALIVE="true")

    async def run():
        first = keepalive.start()
        second = keepalive.start()
        assert first is not None and first is second
        await keepalive.stop()
        assert first.done() and first.cancelled()
        # Stopping twice is harmless.
        await keepalive.stop()

    asyncio.run(run())


def test_a_failed_ping_is_logged_and_swallowed(caplog) -> None:
    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("no route", request=request)

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(refuse)) as client:
            return await keepalive.ping(client, f"{FRONTEND}/health")

    assert asyncio.run(run()) is False
    assert "keepalive ping failed: ConnectTimeout" in caplog.text


def test_an_error_status_counts_as_a_failure_and_logs_no_body(caplog) -> None:
    async def run():
        transport = httpx.MockTransport(lambda r: httpx.Response(429, text="secret body"))
        async with httpx.AsyncClient(transport=transport) as client:
            return await keepalive.ping(client, f"{FRONTEND}/health")

    assert asyncio.run(run()) is False
    assert "HTTP 429" in caplog.text
    assert "secret body" not in caplog.text


def test_a_good_ping() -> None:
    async def run():
        transport = httpx.MockTransport(lambda r: httpx.Response(200, json={"status": "ok"}))
        async with httpx.AsyncClient(transport=transport) as client:
            return await keepalive.ping(client, f"{FRONTEND}/health")

    assert asyncio.run(run()) is True
