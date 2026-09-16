"""The Groq client, and the only place its exceptions are understood.

One client for the process, not one per request: the SDK holds a connection
pool, and rebuilding it per message throws that away.

Every failure leaves here as a `ChatUnavailable` carrying a code and a sentence
safe to show a user. Nothing from the SDK - status codes, request ids, response
bodies, and above all the key - travels any further into the app.
"""
from __future__ import annotations

import logging
import threading

import groq

from app.core.config import settings

logger = logging.getLogger(__name__)

_client: groq.Groq | None = None
_lock = threading.Lock()


class ChatUnavailable(Exception):
    """The assistant could not answer. Carries user-safe wording."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def get_client() -> groq.Groq:
    """Lazily built, so importing this module never requires a key."""
    global _client
    if not settings.GROQ_API_KEY.strip():
        raise ChatUnavailable(
            "NOT_CONFIGURED", "The assistant is not configured on this server."
        )
    if _client is None:
        with _lock:
            if _client is None:
                _client = groq.Groq(
                    api_key=settings.GROQ_API_KEY,
                    timeout=float(settings.CHAT_REQUEST_TIMEOUT_SECONDS),
                    # The SDK retries 408/409/429/5xx itself. Two is enough:
                    # a chat request the user is waiting on should fail fast
                    # rather than retry into a timeout.
                    max_retries=2,
                )
    return _client


def reset_client() -> None:
    """Drop the cached client. Tests patch settings and need a fresh one."""
    global _client
    with _lock:
        _client = None


def _retry_after_seconds(error: Exception) -> int | None:
    """How long until the provider will accept another request.

    Read from the response headers, never from the exception text: a
    provider's message can quote the request body, and the request body is
    the portal's own data. Anything that is not a plain number is discarded,
    so the only thing that can ever reach a user from here is an integer.
    """
    headers = getattr(getattr(error, "response", None), "headers", None)
    if headers is None:
        return None

    for name in ("retry-after", "x-ratelimit-reset-tokens", "x-ratelimit-reset-requests"):
        raw = headers.get(name)
        if not raw:
            continue
        try:
            # Groq writes these as "13.5s" or "1m2.4s"; retry-after is plain
            # seconds. Parse the shapes we know and ignore anything else.
            text = str(raw).strip()
            seconds = 0.0
            if text.endswith("s") and "m" in text:
                minutes, _, rest = text[:-1].partition("m")
                seconds = float(minutes) * 60 + float(rest or 0)
            elif text.endswith("ms"):
                seconds = float(text[:-2]) / 1000
            elif text.endswith("s"):
                seconds = float(text[:-1])
            else:
                seconds = float(text)
        except (TypeError, ValueError):
            continue
        if 0 < seconds <= 3600:
            return max(1, round(seconds))
    return None


def translate(error: Exception) -> ChatUnavailable:
    """SDK exception -> something a person can read.

    Ordered most specific first; a single broad `except APIError` would lose
    the difference between "retry in a moment" and "this will never work".

    Nothing here interpolates the exception into the user-facing string. A
    provider's message can quote the request body, and the request body is the
    portal's own data.
    """
    if isinstance(error, ChatUnavailable):
        return error

    if isinstance(error, groq.AuthenticationError):
        # Operator error, not user error - say nothing about keys to the user.
        logger.error("Groq rejected the API key")
        return ChatUnavailable(
            "NOT_CONFIGURED", "The assistant is not configured correctly."
        )

    if isinstance(error, groq.PermissionDeniedError):
        logger.error("Groq refused the request: the key may lack model access")
        return ChatUnavailable(
            "NOT_CONFIGURED", "The assistant is not configured correctly."
        )

    if isinstance(error, groq.RateLimitError):
        # "Busy" was misleading: this is not load, it is the account's own
        # per-minute allowance with the provider, and the provider says how
        # long until it refills. Telling someone to "try again in a moment"
        # when the honest answer is fourteen seconds just makes them press the
        # button again immediately and fail again.
        wait = _retry_after_seconds(error)
        logger.warning("Groq rate limit reached; refills in %ss", wait or "unknown")
        if wait:
            return ChatUnavailable(
                "RATE_LIMITED",
                "The assistant has used up its allowance for this minute. "
                f"Try again in about {wait} seconds.",
            )
        return ChatUnavailable(
            "RATE_LIMITED",
            "The assistant has used up its allowance for this minute. "
            "Try again shortly.",
        )

    if isinstance(error, groq.NotFoundError):
        # Almost always a model name that does not exist on this account.
        logger.error("Groq has no model named %r", settings.GROQ_MODEL)
        return ChatUnavailable(
            "NOT_CONFIGURED", "The assistant is not configured correctly."
        )

    if isinstance(error, groq.APITimeoutError):
        logger.warning("Groq timed out after %ss", settings.CHAT_REQUEST_TIMEOUT_SECONDS)
        return ChatUnavailable(
            "TIMEOUT", "That took too long. Try asking again, or more narrowly."
        )

    if isinstance(error, groq.APIConnectionError):
        logger.warning("Groq connection failed: %s", type(error).__name__)
        return ChatUnavailable(
            "UNAVAILABLE", "I can't reach the assistant right now. Please try again."
        )

    if isinstance(error, groq.APIStatusError):
        logger.error("Groq returned %s", error.status_code)
        if error.status_code >= 500:
            return ChatUnavailable(
                "UNAVAILABLE", "The assistant is having trouble. Please try again."
            )
        return ChatUnavailable(
            "LLM_ERROR", "I couldn't complete that request. Please try again."
        )

    logger.exception("Unexpected assistant failure")
    return ChatUnavailable(
        "LLM_ERROR", "Something went wrong answering that. Please try again."
    )
