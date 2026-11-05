"""Chunk ORM model (ING-01..ING-04).

A `Chunk` is one structure-aware slice of a parsed book: raw text, the
physical page range and section-heading path it came from, its bge-m3
embedding vector, and a token count. `content_tsv` is a DB-generated
(`Computed`, persisted) `tsvector` column derived from `text` - it is never
set from Python; Postgres recomputes it on every insert/update via the
`to_tsvector('english', text)` expression declared in the `0007_chunks`
migration. The `embedding` column backs an HNSW cosine-similarity index and
`content_tsv` backs a GIN index, so a single row is retrievable by either a
vector similarity query or a full-text `tsquery` (ING-03's dual index).

`app/chunks/repo.py::replace_chunks_for_book` is the only writer of this
table; it deletes and re-inserts a book's full chunk set inside one
transaction so a retried ingestion job never duplicates rows (ING-04).
"""

import uuid
from datetime import datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import Computed, DateTime, ForeignKey, Integer, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB, TSVECTOR, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class Chunk(Base):
    __tablename__ = "chunks"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    book_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("books.id", ondelete="CASCADE"),
        nullable=False,
    )
    chunk_index: Mapped[int] = mapped_column(Integer(), nullable=False)
    text: Mapped[str] = mapped_column(Text(), nullable=False)
    page_start: Mapped[int] = mapped_column(Integer(), nullable=False)
    page_end: Mapped[int] = mapped_column(Integer(), nullable=False)
    section_path: Mapped[list] = mapped_column(JSONB(), nullable=False, server_default="[]")
    token_count: Mapped[int] = mapped_column(Integer(), nullable=False)
    embedding: Mapped[list[float]] = mapped_column(Vector(1024), nullable=False)
    # DB-generated, read-only: never assign this in Python. Postgres
    # recomputes it from `text` via the `Computed(...)` expression in the
    # 0007_chunks migration.
    content_tsv: Mapped[str] = mapped_column(
        TSVECTOR(),
        Computed("to_tsvector('english', text)", persisted=True),
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        UniqueConstraint("book_id", "chunk_index", name="uq_chunks_book_chunk_index"),
    )
