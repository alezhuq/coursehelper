"""Create stub courses and enrollments tables.

Minimal schema the authorization dependencies (`require_course_enrollment`,
`require_course_owner_or_admin`) resolve against. Later migrations extend `courses` with
real course-management columns and starts populating `enrollments`; creating the
tables now lets later migrations add columns instead of creating the tables
from scratch.

Revision ID: 0004_courses_enrollments_stub
Revises: 0003_users_magiclink
Create Date: 2026-07-11

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

# revision identifiers, used by Alembic.
revision: str = "0004_courses_enrollments_stub"
down_revision: str | None = "0003_users_magiclink"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "courses",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "owner_id",
            UUID(as_uuid=True),
            sa.ForeignKey("users.id", name="fk_courses_owner_id_users"),
            nullable=False,
        ),
        sa.Column("code", sa.String(length=32), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("term", sa.String(length=32), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )

    op.create_table(
        "enrollments",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "user_id",
            UUID(as_uuid=True),
            sa.ForeignKey("users.id", name="fk_enrollments_user_id_users"),
            nullable=False,
        ),
        sa.Column(
            "course_id",
            UUID(as_uuid=True),
            sa.ForeignKey("courses.id", name="fk_enrollments_course_id_courses"),
            nullable=False,
        ),
        sa.UniqueConstraint("user_id", "course_id", name="uq_enrollments_user_course"),
    )


def downgrade() -> None:
    op.drop_table("enrollments")
    op.drop_table("courses")
