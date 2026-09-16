"""Chat payloads.

Note what the request does NOT carry: no user id, no role, no scope. Those come
from the bearer token via `CurrentUser`, and accepting them here would make the
whole authorization design decorative.
"""
from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, Field

from app.schemas.common import ORMModel


class ChatSendRequest(BaseModel):
    message: str = Field(min_length=1, max_length=2000)
    # Absent starts a new conversation. Present, and not the caller's own, 404s.
    conversation_id: uuid.UUID | None = None


class ChatMessageOut(ORMModel):
    id: uuid.UUID
    role: str
    content: str
    created_at: datetime


class ConversationOut(ORMModel):
    id: uuid.UUID
    title: str | None = None
    created_at: datetime
    updated_at: datetime


class ConversationDetail(ConversationOut):
    messages: list[ChatMessageOut] = []


class Suggestion(BaseModel):
    """An opener the UI may offer.

    `topic` lets the client put the ones about the page you are on first. It
    is ordering, not permission - the server has already filtered the list to
    what this role can be answered about.
    """

    text: str
    topic: str


class ChatSetup(BaseModel):
    """Why the assistant is off, for the one person who can fix it.

    A provider name, two booleans and a model name - never the key, not even
    a prefix of it, and not its length.
    """

    provider: str
    has_api_key: bool
    flag_enabled: bool
    model: str


class ChatStatus(BaseModel):
    """What the UI needs to decide whether to render the launcher at all.

    Two booleans, because there are two different questions:

      available - does this deployment want an assistant? (`CHAT_ENABLED`)
      enabled   - can it answer right now? (that, AND a key)

    They were one field once, which made "switched off" and "configured but
    keyless" render identically: nothing, anywhere, for anyone. The second
    deserves to be visible; the first does not.
    """

    available: bool
    enabled: bool
    suggestions: list[Suggestion] = []
    # Administrators only. Everyone else gets null: a BDE cannot edit the
    # server's environment, so telling them about it is noise at best.
    setup: ChatSetup | None = None
