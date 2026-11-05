"""Create conversations/messages tables.

Backs the persisted chat pipeline: one continuous conversation per
(user, course) (`uq_conversations_user_course`, the single-thread
design), and its ordered messages with fully-denormalized citation
JSONB spans (`app/chat/schemas.py::CitationSpan`) rather than bare
chunk-id references, so a citation still resolves correctly after a
book's chunks are later re-ingested (`app/chunks/repo.py::
replace_chunks_for_book` replaces chunk rows wholesale, which would
orphan a bare chunk-id foreign key). `message_role` mirrors the
`book_status`/`role_request_status` enum-creation style already used by
0005/0006. `conversation_id` cascades on delete so a deleted conversation
(not currently exposed by any route) automatically cleans up its
messages with no code change elsewhere.

Revision ID: 0008_conversations_messages
Revises: 0007_chunks
Create Date: 2026-07-24

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

# revision identifiers, used by Alembic.
revision: str = "0008_conversations_messages"
down_revision: str | None = "0007_chunks"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

message_role_enum = sa.Enum("user", "assistant", name="message_role")


def upgrade() -> None:
    op.create_table(
        "conversations",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "user_id",
            UUID(as_uuid=True),
            sa.ForeignKey("users.id", name="fk_conversations_user_id_users"),
            nullable=False,
        ),
        sa.Column(
            "course_id",
            UUID(as_uuid=True),
            sa.ForeignKey("courses.id", name="fk_conversations_course_id_courses"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint("user_id", "course_id", name="uq_conversations_user_course"),
    )
    op.create_table(
        "messages",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "conversation_id",
            UUID(as_uuid=True),
            sa.ForeignKey(
                "conversations.id", ondelete="CASCADE", name="fk_messages_conversation_id"
            ),
            nullable=False,
        ),
        sa.Column(
            "role",
            message_role_enum,
            nullable=False,
        ),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("citations", JSONB(), nullable=False, server_default="[]"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_messages_conversation_created", "messages", ["conversation_id", "created_at"]
    )
    op.create_index("ix_messages_conversation_id", "messages", ["conversation_id"])


def downgrade() -> None:
    op.drop_index("ix_messages_conversation_id", table_name="messages")
    op.drop_index("ix_messages_conversation_created", table_name="messages")
    op.drop_table("messages")
    op.drop_table("conversations")
    message_role_enum.drop(op.get_bind(), checkfirst=True)
