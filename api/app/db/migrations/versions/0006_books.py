"""Create books table.

Backs uploaded PDF materials within a course: storage path on the shared
volume, per-course checksum dedupe, and the ingestion status state machine
(`queued`/`processing`/`ready`/`failed`) that
`app/ingestion/tasks.py::ingest_book_stub` drives.

Revision ID: 0006_books
Revises: 0005_role_requests
Create Date: 2026-07-13

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

# revision identifiers, used by Alembic.
revision: str = "0006_books"
down_revision: str | None = "0005_role_requests"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

status_enum = sa.Enum("queued", "processing", "ready", "failed", name="book_status")


def upgrade() -> None:
    op.create_table(
        "books",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "course_id",
            UUID(as_uuid=True),
            sa.ForeignKey("courses.id", name="fk_books_course_id_courses"),
            nullable=False,
        ),
        sa.Column(
            "uploaded_by",
            UUID(as_uuid=True),
            sa.ForeignKey("users.id", name="fk_books_uploaded_by_users"),
            nullable=False,
        ),
        sa.Column("original_filename", sa.String(length=512), nullable=False),
        sa.Column("storage_path", sa.String(length=1024), nullable=False),
        sa.Column("checksum_sha256", sa.String(length=64), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("status", status_enum, nullable=False, server_default="queued"),
        sa.Column("status_error", sa.String(length=2000), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint("course_id", "checksum_sha256", name="uq_books_course_checksum"),
    )


def downgrade() -> None:
    op.drop_table("books")
    status_enum.drop(op.get_bind(), checkfirst=True)
