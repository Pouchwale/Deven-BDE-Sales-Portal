"""Shapes for the ready-to-send request flow.

Shared between customer timelines and lead messaging, since the same
compose-then-record pattern applies to both — see app/services/messaging.py.
"""
from __future__ import annotations

from pydantic import BaseModel, Field


class ComposedMessageOut(BaseModel):
    """A ready-to-send request, with the deep link that opens it."""

    purpose: str
    channel: str
    to: str | None = None
    subject: str | None = None
    body: str
    link: str
    url: str | None = None
    link_configured: bool
    missing_contact: str | None = None
    whatsapp_number: str | None = None


class SentBody(BaseModel):
    purpose: str
    channel: str
    body: str | None = Field(default=None, max_length=4000)
