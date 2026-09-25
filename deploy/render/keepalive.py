"""Keep the Render services awake: GET each URL in PING_URLS.

Render's free web services sleep after 15 minutes without a request, and the
first request after that waits ~a minute for a cold start - long enough for a
sign-in to fail. render.yaml runs this as a cron job every 14 minutes
during working hours.

Point PING_URLS at the frontend's /health: the frontend proxies it to the
backend's /health, so one request keeps both services up.

    PING_URLS=https://portal.onrender.com/health python deploy/render/keepalive.py

Standard library only, so the cron needs no build step. Exits non-zero when
any URL fails, so a failed run shows in the Render dashboard.
"""
from __future__ import annotations

import os
import sys
import time
import urllib.request

TIMEOUT_SECONDS = 90  # a cold start can take ~60s
ATTEMPTS = 3
RETRY_DELAY_SECONDS = 30


def ping(url: str) -> bool:
    for attempt in range(1, ATTEMPTS + 1):
        started = time.monotonic()
        try:
            request = urllib.request.Request(url, headers={"User-Agent": "portal-keepalive"})
            with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
                print(f"{url} -> {response.status} in {time.monotonic() - started:.1f}s")
                return True
        except Exception as exc:  # noqa: BLE001 - reported, then retried
            print(f"{url} attempt {attempt} failed: {exc}", file=sys.stderr)
            # Render may refuse a wake-up outright (429 hibernate-rate-limited);
            # give it a moment before asking again.
            if attempt < ATTEMPTS:
                time.sleep(RETRY_DELAY_SECONDS)
    return False


def main() -> int:
    urls = [u.strip() for u in os.environ.get("PING_URLS", "").split(",") if u.strip()]
    if not urls:
        print("PING_URLS is not set - nothing to ping.", file=sys.stderr)
        return 1
    results = [ping(url) for url in urls]
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
