"""Enable the pgvector extension.

No application tables yet - users/magic_link_requests, the courses/enrollments
stubs, and role_requests each add their own migration on top of this baseline.

Revision ID: 0001_baseline
Revises:
Create Date: 2026-07-11

"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0001_baseline"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")


def downgrade() -> None:
    op.execute("DROP EXTENSION IF EXISTS vector")
