"""Create chunks table.

Backs the parsed/chunked/embedded content of a book: raw chunk text, the
physical page range and section-heading path it came from, its bge-m3
embedding, and a DB-generated `tsvector` column for full-text search. This
is the dual-index write target of the ingestion pipeline (Stage 4) -
`ix_chunks_embedding_hnsw` (pgvector HNSW, cosine ops) and
`ix_chunks_content_tsv` (GIN) let hybrid search query both a vector similarity
search and a keyword full-text search over the same rows.
`uq_chunks_book_chunk_index` plus deterministic uuid5 chunk IDs
(`app/chunks/repo.py::chunk_id`) are the idempotency safety net so a
retried ingestion job never duplicates chunks. `book_id` cascades
on delete so `app/books/repo.py::delete_book` automatically cleans up a
book's chunks with no code change in that module.

Revision ID: 0007_chunks
Revises: 0006_books
Create Date: 2026-07-23

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects.postgresql import JSONB, TSVECTOR, UUID

# revision identifiers, used by Alembic.
revision: str = "0007_chunks"
down_revision: str | None = "0006_books"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "chunks",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "book_id",
            UUID(as_uuid=True),
            sa.ForeignKey("books.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("chunk_index", sa.Integer(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("page_start", sa.Integer(), nullable=False),
        sa.Column("page_end", sa.Integer(), nullable=False),
        sa.Column("section_path", JSONB(), nullable=False, server_default="[]"),
        sa.Column("token_count", sa.Integer(), nullable=False),
        sa.Column("embedding", Vector(1024), nullable=False),
        sa.Column(
            "content_tsv",
            TSVECTOR(),
            sa.Computed("to_tsvector('english', text)", persisted=True),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint("book_id", "chunk_index", name="uq_chunks_book_chunk_index"),
    )
    op.create_index(
        "ix_chunks_embedding_hnsw",
        "chunks",
        ["embedding"],
        postgresql_using="hnsw",
        postgresql_with={"m": 16, "ef_construction": 64},
        postgresql_ops={"embedding": "vector_cosine_ops"},
    )
    op.create_index("ix_chunks_content_tsv", "chunks", ["content_tsv"], postgresql_using="gin")
    op.create_index("ix_chunks_book_id", "chunks", ["book_id"])


def downgrade() -> None:
    op.drop_table("chunks")
