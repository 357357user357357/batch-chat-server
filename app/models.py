from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import DateTime, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


def utcnow() -> datetime:
    """Naive UTC timestamp (SQLite stores no tz info, so keep it naive)."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


class Account(Base):
    """A client account on this instance.

    Multi-account: every account is a full isolated client — its own pair
    key (pairing code), optional own password, and its own data (dialogs,
    messages, batches are stamped with `account_id` and every query is
    filtered by the token's account). The FIRST account is the instance
    owner (migrated from the legacy app_settings identity): only the owner
    manages provider credentials. Accounts survive IP changes and server
    moves — copy the pair code to any new device.
    """

    __tablename__ = "accounts"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    # The pairing secret (full token entropy). Verifying a pair code looks
    # the account up by id and compares this value.
    key: Mapped[str] = mapped_column(String(128))
    label: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # Optional per-account password (pbkdf2 hash). When unset, the account
    # logs in with the instance master password (settings.app_password).
    password_hash: Mapped[str | None] = mapped_column(String(256), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class AuthToken(Base):
    __tablename__ = "auth_tokens"

    token: Mapped[str] = mapped_column(String(64), primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime)
    # Which account this session belongs to (data isolation). Legacy rows
    # (NULL) are bound to the owner account on first use.
    account_id: Mapped[str | None] = mapped_column(
        String(64), ForeignKey("accounts.id"), nullable=True, index=True
    )


class AppSetting(Base):
    """Runtime-configurable key/value overrides (provider API keys etc.),
    editable from the web UI so a container restart is never required."""

    __tablename__ = "app_settings"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str] = mapped_column(Text, default="")
    updated_at: Mapped[datetime | None] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


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
    # Soft-delete tombstone: kept (instead of a hard delete) so other devices
    # can be told "this dialog was deleted" the next time they sync.
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, index=True)
    # Audit trail (master-server archive): WHICH device created this record,
    # which one last modified it, and which one deleted it. Devices identify
    # themselves via the X-Device-Name header (fallback: User-Agent).
    origin_device: Mapped[str | None] = mapped_column(String(64), nullable=True)
    modified_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    deleted_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # Data isolation: which account owns this dialog. Messages and
    # tombstones inherit the scope through conversation_id.
    account_id: Mapped[str | None] = mapped_column(
        String(64), ForeignKey("accounts.id"), nullable=True, index=True
    )
    # Prompt-cache keep-alive toggle (🔥 Cache button in the web UI): when on,
    # the keeper sends near-empty pings for this dialog's cached prefix.
    keepalive_enabled: Mapped[bool] = mapped_column(default=False)

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
    # Soft-delete tombstone for individual Q/A removals (web UI): the text
    # stays archived in the DB, but the message no longer shows up in the
    # dialog, in sync pulls, or on other devices.
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    # Which device deleted this Q/A (audit trail on the master server).
    deleted_by: Mapped[str | None] = mapped_column(String(64), nullable=True)

    conversation: Mapped["Conversation"] = relationship(back_populates="messages")


class MessageTombstone(Base):
    """Remembers that a specific question/answer was deleted inside a dialog.

    The phone app syncs whole dialogs (full message list, no per-message ids),
    so without this record the next phone push would re-upload a message the
    user deleted from the web. Keyed by (conversation_id, role, content) and
    checked during sync push/upsert so deleted Q/A stay deleted everywhere,
    while the original text remains archived here in the database.
    """

    __tablename__ = "message_tombstones"

    id: Mapped[int] = mapped_column(primary_key=True)
    conversation_id: Mapped[int] = mapped_column(
        ForeignKey("conversations.id"), index=True
    )
    role: Mapped[str] = mapped_column(String(16))
    content: Mapped[str] = mapped_column(Text)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime, default=utcnow)
    # Which device deleted this Q/A (audit trail on the master server).
    deleted_by: Mapped[str | None] = mapped_column(String(64), nullable=True)

    conversation: Mapped["Conversation"] = relationship()


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
    # Data isolation: which account submitted this batch.
    account_id: Mapped[str | None] = mapped_column(
        String(64), ForeignKey("accounts.id"), nullable=True, index=True)

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