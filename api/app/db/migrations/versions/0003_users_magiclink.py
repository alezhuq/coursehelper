"""Create users and magic_link_requests tables.

Adds the identity model shared by both auth paths (CAS and
magic-link): `users` (with a Postgres `role` enum defaulting to
`student`, and an `auth_method` enum recording which path
created the row) and `magic_link_requests` (the single-use guard for
magic-link tokens - see `app/auth/magic_link.py`).

Revision ID: 0003_users_magiclink
Revises: 0002_procrastinate_schema
Create Date: 2026-07-11

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

# revision identifiers, used by Alembic.
revision: str = "0003_users_magiclink"
down_revision: str | None = "0002_procrastinate_schema"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

role_enum = sa.Enum("student", "professor", "admin", name="role")
auth_method_enum = sa.Enum("cas", "magic_link", name="auth_method")


def upgrade() -> None:
    # Deliberately not pre-creating the enum types here: op.create_table()
    # below already issues `CREATE TYPE ... AS ENUM (...)` for any Enum
    # column automatically. Doing both would double-CREATE the same type
    # and fail with "type already exists".
    op.create_table(
        "users",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("computing_id", sa.String(length=32), nullable=True),
        sa.Column("email", sa.String(length=320), nullable=False),
        sa.Column("display_name", sa.String(length=255), nullable=False),
        sa.Column(
            "role",
            role_enum,
            nullable=False,
            server_default="student",
        ),
        sa.Column("auth_method", auth_method_enum, nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint("computing_id", name="uq_users_computing_id"),
        sa.UniqueConstraint("email", name="uq_users_email"),
    )

    op.create_table(
        "magic_link_requests",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("email", sa.String(length=320), nullable=False),
        sa.Column("nonce", sa.String(length=64), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("nonce", name="uq_magic_link_requests_nonce"),
    )


def downgrade() -> None:
    op.drop_table("magic_link_requests")
    op.drop_table("users")
    auth_method_enum.drop(op.get_bind(), checkfirst=True)
    role_enum.drop(op.get_bind(), checkfirst=True)
