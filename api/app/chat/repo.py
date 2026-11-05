"""Conversation/message persistence, mirroring `app/search/repo.py`.

`get_or_create_conversation` keeps a select-first fast path and falls back
to `INSERT ... ON CONFLICT DO NOTHING` plus a re-select, so two concurrent
first visits never race into an `IntegrityError` on the unique constraint.

No internal commit anywhere in this module - the route owns the
transactions ("persist immediately" semantics): the user turn is
committed as soon as it's received (regardless of what follows), and the
assistant turn is only inserted after generation succeeds, in its own fresh
transaction after the LLM stream, so a mid-stream failure never leaves a
partial/incorrect assistant turn persisted (concurrency guarantee).
"""

import uuid

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.chat.models import Conversation, Message
from app.chat.schemas import CitationSpan


async def get_or_create_conversation(
    session: AsyncSession, user_id: uuid.UUID, course_id: uuid.UUID
) -> Conversation:
    """Find the single (user, course) conversation, creating it on first
    contact. One continuous conversation per (user, course),
    enforced by `uq_conversations_user_course`."""
    result = await session.execute(
        select(Conversation).where(
            Conversation.user_id == user_id, Conversation.course_id == course_id
        )
    )
    existing = result.scalar_one_or_none()
    if existing is not None:
        return existing

    # Race-safe create: a concurrent first visit may insert between the
    # select above and this insert. ON CONFLICT DO NOTHING waits on the
    # other transaction's uncommitted row, then yields no insert, and the
    # re-select returns the single row the unique constraint allows.
    await session.execute(
        pg_insert(Conversation)
        .values(user_id=user_id, course_id=course_id)
        .on_conflict_do_nothing(constraint="uq_conversations_user_course")
    )
    result = await session.execute(
        select(Conversation).where(
            Conversation.user_id == user_id, Conversation.course_id == course_id
        )
    )
    return result.scalar_one()


async def get_owned_conversation(
    session: AsyncSession, conversation_id: uuid.UUID, user_id: uuid.UUID, course_id: uuid.UUID
) -> Conversation | None:
    """Re-derive conversation ownership strictly from (user_id, course_id):
    a `conversation_id` not owned by this exact pair returns None so the
    caller can 404 before any turn is loaded or persisted."""
    result = await session.execute(
        select(Conversation).where(
            Conversation.id == conversation_id,
            Conversation.user_id == user_id,
            Conversation.course_id == course_id,
        )
    )
    return result.scalar_one_or_none()


async def get_latest_message(session: AsyncSession, conversation_id: uuid.UUID) -> Message | None:
    """Return the newest `Message` of the conversation, or None if empty."""
    result = await session.execute(
        select(Message)
        .where(Message.conversation_id == conversation_id)
        .order_by(Message.created_at.desc())
        .limit(1)
    )
    return result.scalar_one_or_none()


async def load_recent_turns(
    session: AsyncSession,
    conversation_id: uuid.UUID,
    limit: int = 3,
    exclude_message_id: uuid.UUID | None = None,
) -> list[dict]:
    """Return the last `limit` (user, assistant) turn pairs as OpenAI-shaped
    `{role, content}` dicts, oldest-first (only the last K=3 pairs are sent verbatim per generation
    call). `exclude_message_id` drops one message (the unanswered user turn
    being retried) so it never appears twice in the prompt."""
    stmt = select(Message).where(Message.conversation_id == conversation_id)
    if exclude_message_id is not None:
        stmt = stmt.where(Message.id != exclude_message_id)
    result = await session.execute(stmt.order_by(Message.created_at.desc()).limit(limit * 2))
    rows = list(result.scalars().all())
    rows.reverse()
    return [{"role": row.role, "content": row.content} for row in rows]


async def load_all_messages(session: AsyncSession, conversation_id: uuid.UUID) -> list[Message]:
    result = await session.execute(
        select(Message)
        .where(Message.conversation_id == conversation_id)
        .order_by(Message.created_at.asc())
    )
    return list(result.scalars().all())


async def insert_user_message(
    session: AsyncSession, conversation_id: uuid.UUID, content: str
) -> Message:
    message = Message(conversation_id=conversation_id, role="user", content=content, citations=[])
    session.add(message)
    await session.flush()
    return message


async def insert_assistant_message(
    session: AsyncSession,
    conversation_id: uuid.UUID,
    content: str,
    citations: list[CitationSpan],
) -> Message:
    message = Message(
        conversation_id=conversation_id,
        role="assistant",
        content=content,
        citations=[span.model_dump(mode="json") for span in citations],
    )
    session.add(message)
    await session.flush()
    return message


async def delete_messages_for_conversation(
    session: AsyncSession, conversation_id: uuid.UUID
) -> int:
    """Delete every `Message` row for `conversation_id`, returning the
    affected row count. The `Conversation` row itself is intentionally left
    standing: an open ChatPage client is holding the `conversation_id` in
    its own state, and `get_or_create_conversation` returns the exact same
    row on the client's very next send. Deleting the conversation row too
    would make that id foreign, turning the next send into a 404 the
    student would have to reload past."""
    result = await session.execute(
        delete(Message).where(Message.conversation_id == conversation_id)
    )
    await session.flush()
    return result.rowcount
