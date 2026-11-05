"""Conversation and Message ORM models.

`Conversation` is one continuous conversation per (user, course) - the
design decision to keep a single ongoing thread per course rather than a
list of past conversations. `Message` records both turns; `citations` is a
JSONB list of fully-denormalized `CitationSpan` dicts (marker, book_id,
original_filename, page_start, page_end, section_path) rather than bare
chunk-id references, so a citation still resolves correctly to the right
book/page even after that book's chunks are later re-ingested (a
re-ingestion replaces chunk rows wholesale per
`app/chunks/repo.py::replace_chunks_for_book`, which would orphan a bare
chunk-id foreign key).

Mirrors `app/chunks/models.py`/`app/roles/models.py` conventions: UUID
primary keys, `Mapped[]` typed columns, `server_default=func.now()` for
timestamps.
"""

import uuid
from datetime import datetime

from sqlalchemy import DateTime, Enum, ForeignKey, Index, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class Conversation(Base):
    __tablename__ = "conversations"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", name="fk_conversations_user_id_users"),
        nullable=False,
    )
    course_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("courses.id", name="fk_conversations_course_id_courses"),
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        # Single continuous conversation per (user, course).
        # get_or_create_conversation relies on
        # this constraint to make "find or create" race-safe.
        UniqueConstraint("user_id", "course_id", name="uq_conversations_user_course"),
    )


class Message(Base):
    __tablename__ = "messages"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    conversation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("conversations.id", ondelete="CASCADE", name="fk_messages_conversation_id"),
        nullable=False,
    )
    role: Mapped[str] = mapped_column(
        Enum("user", "assistant", name="message_role"), nullable=False
    )
    content: Mapped[str] = mapped_column(Text(), nullable=False)
    # Fully-denormalized CitationSpan dicts (see app/chat/schemas.py). Empty
    # list for user turns and for the empty-retrieval refusal turn.
    citations: Mapped[list] = mapped_column(JSONB(), nullable=False, server_default="[]")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        Index("ix_messages_conversation_created", "conversation_id", "created_at"),
        Index("ix_messages_conversation_id", "conversation_id"),
    )
