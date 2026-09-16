"""The in-portal assistant.

`user_id`, `role` and `scope` are never read from a request body - they come
from the bearer token through `CurrentUser` and `VisibilityScope`, exactly as
every other module does it. See docs/CHATBOT_IMPLEMENTATION_PLAN.md.
"""
from __future__ import annotations

import json
import uuid
from typing import Iterator

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse

from app.core import authority
from app.core.config import settings
from app.core.constants import ErrorCode
from app.core.deps import CurrentUser, DbSession, VisibilityScope, client_ip
from app.core.errors import ApiError
from app.core.ratelimit import SlidingWindowLimiter
from app.core.constants import ADMIN_ROLES
from app.schemas.chat import (
    ChatSendRequest,
    ChatSetup,
    ChatStatus,
    ConversationDetail,
    ConversationOut,
)
from app.schemas.common import Message
from app.services.chat import context as ctx_store
from app.services.chat import prompt as prompt_builder
from app.services.chat import session as chat_session

router = APIRouter(prefix="/chat", tags=["chat"])

# Per-process, like the login limiter: it stops one person hammering the
# assistant, not a distributed attack. Same known limitation, same reasoning.
message_limiter = SlidingWindowLimiter(
    max_attempts=settings.CHAT_RATE_LIMIT_MESSAGES,
    window_seconds=settings.CHAT_RATE_LIMIT_WINDOW_SECONDS,
)


def _require_enabled() -> None:
    if not settings.chat_enabled:
        raise ApiError(
            ErrorCode.VALIDATION_ERROR,
            "The assistant is not enabled on this server.",
            status_code=503,
        )


@router.get("/status", response_model=ChatStatus)
def chat_status(actor: CurrentUser) -> ChatStatus:
    """Whether to show the launcher, and what to suggest.

    Deliberately not gated on `_require_enabled`: the UI needs a truthful
    `false` rather than an error when the feature is off.
    """
    # An admin also gets the *reason* it is off. Without this the feature is
    # invisible with no explanation, which is indistinguishable from a bug -
    # the Settings page renders this so somebody can act on it.
    setup = (
        ChatSetup(
            provider=settings.CHAT_PROVIDER,
            has_api_key=bool(settings.GROQ_API_KEY.strip()),
            flag_enabled=settings.CHAT_ENABLED,
            model=settings.GROQ_MODEL,
        )
        if actor.role in ADMIN_ROLES
        else None
    )

    if not settings.chat_enabled:
        return ChatStatus(
            available=settings.CHAT_ENABLED,
            enabled=False,
            suggestions=[],
            setup=setup,
        )
    return ChatStatus(
        available=True,
        enabled=True,
        suggestions=prompt_builder.suggested_prompts(actor),
        setup=setup,
    )


@router.post("/messages")
def send_message(
    payload: ChatSendRequest,
    request: Request,
    actor: CurrentUser,
    db: DbSession,
    scope: VisibilityScope,
) -> StreamingResponse:
    """Ask a question. Responds as a Server-Sent Events stream.

    A sync generator on purpose: the whole app is synchronous and the ORM work
    inside the loop must not run on the event loop.
    """
    _require_enabled()

    allowed, retry_after = message_limiter.check(str(actor.id))
    if not allowed:
        raise ApiError(
            ErrorCode.RATE_LIMITED,
            "You're sending messages very quickly. Give it a moment.",
            status_code=429,
            details={"retry_after_seconds": retry_after},
        )

    if payload.conversation_id is not None:
        conversation = ctx_store.get_conversation(db, actor, payload.conversation_id)
    else:
        conversation = ctx_store.start_conversation(db, actor, payload.message)

    # Department scope is a separate axis from people scope - see the plan's
    # role matrix. Both are derived here, never supplied.
    department_scope = authority.feedback_department_scope(actor)

    events = chat_session.answer(
        db,
        actor,
        conversation,
        payload.message.strip(),
        scope=scope,
        department_scope=department_scope,
        ip_address=client_ip(request),
    )

    return StreamingResponse(
        _as_sse(events),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
            # Nginx buffers SSE into uselessness without this.
            "X-Accel-Buffering": "no",
        },
    )


def _as_sse(events: Iterator[chat_session.Event]) -> Iterator[str]:
    for event in events:
        yield f"event: {event.type}\ndata: {json.dumps(event.data, default=str)}\n\n"


@router.get("/conversations", response_model=list[ConversationOut])
def list_conversations(actor: CurrentUser, db: DbSession) -> list[ConversationOut]:
    return [
        ConversationOut.model_validate(row)
        for row in ctx_store.list_conversations(db, actor)
    ]


@router.get("/conversations/{conversation_id}", response_model=ConversationDetail)
def get_conversation(
    conversation_id: uuid.UUID, actor: CurrentUser, db: DbSession
) -> ConversationDetail:
    conversation = ctx_store.get_conversation(db, actor, conversation_id)
    return ConversationDetail.model_validate(conversation)


@router.delete("/conversations/{conversation_id}", response_model=Message)
def delete_conversation(
    conversation_id: uuid.UUID, actor: CurrentUser, db: DbSession
) -> Message:
    conversation = ctx_store.get_conversation(db, actor, conversation_id)
    db.delete(conversation)
    db.commit()
    return Message(message="Conversation deleted.")
