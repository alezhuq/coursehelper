"""Create role_requests table.

Backs the in-app professor-role request/approval flow: a student submits a
request (stored pending), an admin approves (promoting the requester to
professor) or denies it, and the decision is recorded via
`decided_at`/`decided_by`. A partial unique index enforces at most one
pending request per user.

Revision ID: 0005_role_requests
Revises: 0004_courses_enrollments_stub
Create Date: 2026-07-12

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

# revision identifiers, used by Alembic.
revision: str = "0005_role_requests"
down_revision: str | None = "0004_courses_enrollments_stub"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

status_enum = sa.Enum("pending", "approved", "denied", name="role_request_status")


def upgrade() -> None:
    op.create_table(
        "role_requests",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "user_id",
            UUID(as_uuid=True),
            sa.ForeignKey("users.id", name="fk_role_requests_user_id_users"),
            nullable=False,
        ),
        sa.Column(
            "status",
            status_enum,
            nullable=False,
            server_default="pending",
        ),
        sa.Column(
            "requested_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "decided_by",
            UUID(as_uuid=True),
            sa.ForeignKey("users.id", name="fk_role_requests_decided_by_users"),
            nullable=True,
        ),
    )
    op.create_index(
        "uq_role_requests_one_pending_per_user",
        "role_requests",
        ["user_id"],
        unique=True,
        postgresql_where=sa.text("status = 'pending'"),
    )


def downgrade() -> None:
    op.drop_index("uq_role_requests_one_pending_per_user", table_name="role_requests")
    op.drop_table("role_requests")
    status_enum.drop(op.get_bind(), checkfirst=True)
