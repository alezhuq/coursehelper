"""Book ORM model.

A `Book` records one uploaded PDF within a course: where its bytes live on
the shared storage volume (`storage_path`), its per-course dedupe checksum,
and its ingestion status. `BookStatus` is the ingestion state machine that
`app/ingestion/tasks.py::ingest_book_stub` drives:
queued -> processing -> ready, or -> failed (with a readable
`status_error`). This model and its states are the stable contract the
upload routes and the ingestion task both write.
"""

import enum
import uuid
from datetime import datetime

from sqlalchemy import BigInteger, DateTime, Enum, ForeignKey, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class BookStatus(enum.StrEnum):
    queued = "queued"
    processing = "processing"
    ready = "ready"
    failed = "failed"


class Book(Base):
    __tablename__ = "books"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    course_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("courses.id", name="fk_books_course_id_courses"),
        nullable=False,
    )
    uploaded_by: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", name="fk_books_uploaded_by_users"),
        nullable=False,
    )
    original_filename: Mapped[str] = mapped_column(String(512), nullable=False)
    storage_path: Mapped[str] = mapped_column(String(1024), nullable=False)
    checksum_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger(), nullable=False)
    status: Mapped[BookStatus] = mapped_column(
        Enum(BookStatus, name="book_status"),
        nullable=False,
        default=BookStatus.queued,
        server_default=BookStatus.queued.value,
    )
    status_error: Mapped[str | None] = mapped_column(String(2000), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        # Per-course checksum dedupe: the same PDF may be uploaded
        # to two different courses, but never uploaded twice to the same
        # course. Enforced at the database level (not SELECT-then-INSERT)
        # so two concurrent uploads of the identical file race safely.
        UniqueConstraint("course_id", "checksum_sha256", name="uq_books_course_checksum"),
    )
