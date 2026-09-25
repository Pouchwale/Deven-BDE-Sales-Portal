"""The self-ping that keeps a Render free-tier backend awake."""
from __future__ import annotations

import asyncio

import httpx
import pytest

from app.core.config import settings
from app.services import keepalive


def _configure(monkeypatch, *, enabled: bool, url: str = "", interval: int = 600) -> None:
    monkeypatch.setattr(settings, "ENABLE_KEEPALIVE", enabled)
    monkeypatch.setattr(settings, "SELF_PUBLIC_URL", url)
    monkeypatch.setattr(settings, "KEEPALIVE_INTERVAL_SECONDS", interval)


def test_off_by_default() -> None:
    assert settings.ENABLE_KEEPALIVE is False


def test_nothing_starts_when_disabled(monkeypatch) -> None:
    _configure(monkeypatch, enabled=False, url="https://portal.onrender.com/health")

    async def run():
        return keepalive.start()

    assert asyncio.run(run()) is None


def test_blank_url_is_skipped_with_a_warning(monkeypatch, caplog) -> None:
    _configure(monkeypatch, enabled=True, url="")

    async def run():
        return keepalive.start()

    assert asyncio.run(run()) is None
    assert "SELF_PUBLIC_URL is blank" in caplog.text


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
    _configure(monkeypatch, enabled=True, url=url)
    assert keepalive.target_url() is None


def test_health_is_added_to_a_bare_host(monkeypatch) -> None:
    _configure(monkeypatch, enabled=True, url="https://portal.onrender.com/")
    assert keepalive.target_url() == "https://portal.onrender.com/health"
    _configure(monkeypatch, enabled=True, url="https://portal.onrender.com/health")
    assert keepalive.target_url() == "https://portal.onrender.com/health"


def test_one_loop_only_and_cancelled_cleanly(monkeypatch) -> None:
    _configure(monkeypatch, enabled=True, url="https://portal.onrender.com/health")

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
            return await keepalive.ping(client, "https://portal.onrender.com/health")

    assert asyncio.run(run()) is False
    assert "keepalive ping failed: ConnectTimeout" in caplog.text


def test_an_error_status_counts_as_a_failure_and_logs_no_body(caplog) -> None:
    async def run():
        transport = httpx.MockTransport(lambda r: httpx.Response(503, text="secret body"))
        async with httpx.AsyncClient(transport=transport) as client:
            return await keepalive.ping(client, "https://portal.onrender.com/health")

    assert asyncio.run(run()) is False
    assert "HTTP 503" in caplog.text
    assert "secret body" not in caplog.text


def test_a_good_ping(caplog) -> None:
    async def run():
        transport = httpx.MockTransport(lambda r: httpx.Response(200, json={"status": "ok"}))
        async with httpx.AsyncClient(transport=transport) as client:
            return await keepalive.ping(client, "https://portal.onrender.com/health")

    assert asyncio.run(run()) is True
