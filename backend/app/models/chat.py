"""The assistant's conversations, messages and tool-call metadata.

Mirrors app/db/sql/0005_chat.sql. Note what is absent: no tool RESULT is
stored anywhere. The rows a tool returned belong to the authority model that
produced them, and copying them here would put customer data outside it.
"""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import GUID, Base, JSONType, TimestampType, new_uuid, utcnow


class ChatConversation(Base):
    __tablename__ = "chat_conversations"

    id: Mapped[uuid.UUID] = mapped_column(GUID, primary_key=True, default=new_uuid)
    user_id: Mapped[uuid.UUID] = mapped_column(
        GUID, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    title: Mapped[str | None] = mapped_column(String(120))

    # See the column comment in the migration: this is how a permission change
    # invalidates replayed tool results without throwing away the transcript.
    permission_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        TimestampType, nullable=False, default=utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        TimestampType, nullable=False, default=utcnow, onupdate=utcnow
    )

    messages: Mapped[list[ChatMessage]] = relationship(
        "ChatMessage",
        back_populates="conversation",
        cascade="all, delete-orphan",
        order_by="ChatMessage.created_at",
    )


class ChatMessage(Base):
    __tablename__ = "chat_messages"

    id: Mapped[uuid.UUID] = mapped_column(GUID, primary_key=True, default=new_uuid)
    conversation_id: Mapped[uuid.UUID] = mapped_column(
        GUID, ForeignKey("chat_conversations.id", ondelete="CASCADE"), nullable=False
    )
    role: Mapped[str] = mapped_column(String(20), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)

    input_tokens: Mapped[int | None] = mapped_column(Integer)
    output_tokens: Mapped[int | None] = mapped_column(Integer)

    created_at: Mapped[datetime] = mapped_column(
        TimestampType, nullable=False, default=utcnow
    )

    conversation: Mapped[ChatConversation] = relationship(
        "ChatConversation", back_populates="messages"
    )
    tool_calls: Mapped[list[ChatToolCall]] = relationship(
        "ChatToolCall",
        back_populates="message",
        cascade="all, delete-orphan",
        order_by="ChatToolCall.created_at",
    )


class ChatToolCall(Base):
    """One tool invocation. Arguments and a row count - never the rows."""

    __tablename__ = "chat_tool_calls"

    id: Mapped[uuid.UUID] = mapped_column(GUID, primary_key=True, default=new_uuid)
    message_id: Mapped[uuid.UUID] = mapped_column(
        GUID, ForeignKey("chat_messages.id", ondelete="CASCADE"), nullable=False
    )
    tool_name: Mapped[str] = mapped_column(String(60), nullable=False)

    arguments: Mapped[dict | None] = mapped_column(JSONType)
    row_count: Mapped[int | None] = mapped_column(Integer)

    ok: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    error_code: Mapped[str | None] = mapped_column(String(40))
    duration_ms: Mapped[int | None] = mapped_column(Integer)

    created_at: Mapped[datetime] = mapped_column(
        TimestampType, nullable=False, default=utcnow
    )

    message: Mapped[ChatMessage] = relationship("ChatMessage", back_populates="tool_calls")
