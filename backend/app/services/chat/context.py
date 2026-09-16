"""Conversation persistence and the history that gets replayed.

Two rules govern what goes back to the model:

  * Permissions are never remembered. Scope is recomputed from the
    authenticated user on every request; nothing about authorization is read
    from the stored conversation.

  * A conversation whose owner's permissions have changed replays as text only.
    Old assistant text may quote figures they could see at the time, which is
    the same exposure as the transcript itself; what must not happen is feeding
    the model structured rows fetched under permissions that no longer hold.
"""
from __future__ import annotations

import hashlib
import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.errors import not_found
from app.models.chat import ChatConversation, ChatMessage, ChatToolCall
from app.models.org import User

MAX_TITLE = 120


def permission_fingerprint(actor: User) -> str:
    """Everything that decides what this user may see, hashed.

    Role, manager and department headship are exactly the inputs to
    `visible_user_ids` and `feedback_department_scope`. If none of them moved,
    the scope cannot have changed.
    """
    raw = "|".join(
        [
            str(actor.role),
            str(actor.manager_id or ""),
            str(actor.heads_department_id or ""),
            str(actor.is_active),
        ]
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def get_conversation(db: Session, actor: User, conversation_id: uuid.UUID) -> ChatConversation:
    """404 for somebody else's conversation - never 403, matching the house
    convention for ids outside your scope."""
    conversation = db.execute(
        select(ChatConversation).where(
            ChatConversation.id == conversation_id,
            ChatConversation.user_id == actor.id,
        )
    ).scalars().one_or_none()
    if conversation is None:
        raise not_found("Conversation not found.")
    return conversation


def list_conversations(db: Session, actor: User, *, limit: int = 30) -> list[ChatConversation]:
    return list(
        db.execute(
            select(ChatConversation)
            .where(ChatConversation.user_id == actor.id)
            .order_by(ChatConversation.updated_at.desc())
            .limit(limit)
        ).scalars()
    )


def start_conversation(db: Session, actor: User, first_message: str) -> ChatConversation:
    conversation = ChatConversation(
        user_id=actor.id,
        title=_title_from(first_message),
        permission_fingerprint=permission_fingerprint(actor),
    )
    db.add(conversation)
    db.flush()
    return conversation


def _title_from(text: str) -> str:
    clean = " ".join(text.split())
    return clean[: MAX_TITLE - 1] + "…" if len(clean) > MAX_TITLE else clean


def add_message(
    db: Session,
    conversation: ChatConversation,
    *,
    role: str,
    content: str,
    input_tokens: int | None = None,
    output_tokens: int | None = None,
) -> ChatMessage:
    message = ChatMessage(
        conversation_id=conversation.id,
        role=role,
        content=content,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
    )
    db.add(message)
    db.flush()
    return message


def record_tool_call(
    db: Session,
    message: ChatMessage,
    *,
    name: str,
    arguments: dict,
    outcome: dict,
) -> ChatToolCall:
    """Arguments and a row count. Never the rows - see models/chat.py."""
    call = ChatToolCall(
        message_id=message.id,
        tool_name=name,
        arguments=arguments or {},
        row_count=outcome.get("row_count"),
        ok=bool(outcome.get("ok")),
        error_code=(outcome.get("error") or {}).get("code"),
        duration_ms=outcome.get("duration_ms"),
    )
    db.add(call)
    return call


def build_history(
    db: Session, conversation: ChatConversation, actor: User
) -> list[dict]:
    """The prior turns to replay, oldest first.

    Only stored text is replayed. Tool results are not persisted at all, so
    "replaying tool results" means the current turn's, held in memory by the
    session - by construction, nothing stale can come back from the database.
    """
    limit = settings.CHAT_HISTORY_MESSAGES
    rows = list(
        db.execute(
            select(ChatMessage)
            .where(ChatMessage.conversation_id == conversation.id)
            .order_by(ChatMessage.created_at.desc())
            .limit(limit)
        ).scalars()
    )
    rows.reverse()

    history: list[dict] = []
    for row in rows:
        if not row.content.strip():
            continue
        history.append(
            {
                "role": "user" if row.role == "USER" else "assistant",
                "content": row.content,
            }
        )

    # The API requires the first message to be from the user. A history that
    # begins with an assistant turn - possible once trimming bites - is invalid.
    while history and history[0]["role"] != "user":
        history.pop(0)

    return history


def refresh_fingerprint(conversation: ChatConversation, actor: User) -> bool:
    """Update the stored fingerprint. Returns True when it had changed."""
    current = permission_fingerprint(actor)
    if conversation.permission_fingerprint == current:
        return False
    conversation.permission_fingerprint = current
    return True
