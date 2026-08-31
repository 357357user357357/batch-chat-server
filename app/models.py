from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import DateTime, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


def utcnow() -> datetime:
    """Naive UTC timestamp (SQLite stores no tz info, so keep it naive)."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


class AuthToken(Base):
    __tablename__ = "auth_tokens"

    token: Mapped[str] = mapped_column(String(64), primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime)


class Conversation(Base):
    __tablename__ = "conversations"

    id: Mapped[int] = mapped_column(primary_key=True)
    # id used by the Android app (AsyncStorage "openrouter.dialogs.v1" / batches v1)
    external_id: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    # "chat" (regular dialog) or "batch" (OpenRouter async batch run)
    kind: Mapped[str] = mapped_column(String(16), default="chat")
    # model used for the whole conversation (app stores one model per dialog)
    model: Mapped[str | None] = mapped_column(String(255), nullable=True)
    title: Mapped[str] = mapped_column(String(255), default="New chat")
    created_at: Mapped[datetime | None] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime | None] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)

    messages: Mapped[list["Message"]] = relationship(
        back_populates="conversation",
        cascade="all, delete-orphan",
        order_by="Message.id",
    )


class Message(Base):
    __tablename__ = "messages"

    id: Mapped[int] = mapped_column(primary_key=True)
    conversation_id: Mapped[int] = mapped_column(ForeignKey("conversations.id"))
    # role: "user" | "assistant" | "system"
    role: Mapped[str] = mapped_column(String(16))
    # model used for assistant messages (for user messages usually NULL)
    model: Mapped[str | None] = mapped_column(String(255), nullable=True)
    content: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime | None] = mapped_column(DateTime, default=utcnow)

    conversation: Mapped["Conversation"] = relationship(back_populates="messages")


class BatchJob(Base):
    """A JSONL-driven async batch submitted to the OpenRouter Batch API.

    `external_id` is the OpenRouter batch id. Terminal statuses come from
    OpenRouter: completed / failed / expired / cancelled.
    """

    __tablename__ = "batch_jobs"

    id: Mapped[int] = mapped_column(primary_key=True)
    external_id: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    model: Mapped[str] = mapped_column(String(255))
    title: Mapped[str] = mapped_column(String(255), default="Batch")
    endpoint: Mapped[str] = mapped_column(String(255), default="/v1/chat/completions")
    status: Mapped[str] = mapped_column(String(32), default="pending")
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Full OpenRouter batch response (status/counts/results) once received
    results_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    # The parsed requests (custom_id + body) submitted to OpenRouter
    requests_json: Mapped[str] = mapped_column(Text, default="[]")
    total_items: Mapped[int] = mapped_column(default=0)
    completed_items: Mapped[int] = mapped_column(default=0)
    failed_items: Mapped[int] = mapped_column(default=0)
    created_at: Mapped[datetime | None] = mapped_column(DateTime, default=utcnow)
    finalized_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    updated_at: Mapped[datetime | None] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)
    conversation_id: Mapped[int | None] = mapped_column(
        ForeignKey("conversations.id"), nullable=True)

    conversation: Mapped[Optional["Conversation"]] = relationship()

    items: Mapped[list["BatchItem"]] = relationship(
        back_populates="batch_job",
        cascade="all, delete-orphan",
        order_by="BatchItem.id",
    )


class BatchItem(Base):
    """One JSONL line of a batch job (custom_id keyed result)."""

    __tablename__ = "batch_items"

    id: Mapped[int] = mapped_column(primary_key=True)
    batch_job_id: Mapped[int] = mapped_column(ForeignKey("batch_jobs.id"))
    custom_id: Mapped[str] = mapped_column(String(255))
    prompt: Mapped[str] = mapped_column(Text, default="")
    # pending | completed | failed
    status: Mapped[str] = mapped_column(String(16), default="pending")
    answer: Mapped[str | None] = mapped_column(Text, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)

    batch_job: Mapped["BatchJob"] = relationship(back_populates="items")