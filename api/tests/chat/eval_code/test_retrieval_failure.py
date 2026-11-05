"""CR-01 regression test: a retrieval-step exception (not just condensation)
surfaces as a graceful SSE `error` event, never an unhandled exception mid-stream.

This covers the fix in commit 94f80c0 that broadened the exception handler in
routes.py to catch failures during embed_batch, vector_arm, keyword_arm, and
fuse_rrf (lines 134-152 in routes.py), not just LLMCondenseError from condensation.

Reuses the same monkeypatch convention and seeding helpers as test_condensation.py.
"""

import json
import uuid
from typing import Any

import pytest
import pytest_asyncio
from httpx import AsyncClient
from testcontainers.postgres import PostgresContainer

from app.books.models import Book, BookStatus
from app.chunks.repo import replace_chunks_for_book
from app.courses.models import Course
from app.db.base import async_session_factory
from app.enrollments.models import Enrollment
from app.users.models import Role, User
from app.users.repo import upsert_user

CSRF_HEADERS = {"X-Requested-With": "fetch"}
MITOCHONDRIA_TEXT = "mitochondria are the powerhouse of the cell"


def _unit_vector(dim: int, hot_index: int) -> list[float]:
    vec = [0.0] * dim
    vec[hot_index] = 1.0
    return vec


async def _make_user(session, email: str, role: Role = Role.professor) -> User:
    user = await upsert_user(
        session,
        email=email,
        computing_id=email.split("@")[0],
        auth_method="cas",
        display_name=email.split("@")[0],
    )
    user.role = role
    await session.flush()
    return user


async def _make_course(session, owner: User, code: str) -> Course:
    course = Course(owner_id=owner.id, code=code, title="Test Course", term="Fall 2026")
    session.add(course)
    await session.flush()
    return course


async def _make_book(session, course: Course, owner: User, checksum: str) -> Book:
    book = Book(
        course_id=course.id,
        uploaded_by=owner.id,
        original_filename="textbook.pdf",
        storage_path=f"/data/books/{checksum}.pdf",
        checksum_sha256=checksum,
        size_bytes=1024,
        status=BookStatus.ready,
    )
    session.add(book)
    await session.flush()
    return book


async def _login(client: AsyncClient, computing_id: str, role: str) -> None:
    response = await client.post(
        "/test-auth/login", json={"computing_id": computing_id, "role": role}
    )
    assert response.status_code == 200


def _fake_embed_batch_factory(recorder: list[str]):
    def _fake_embed_batch(texts: list[str]) -> list[list[float]]:
        recorder.extend(texts)
        return [_unit_vector(1024, 0) for _ in texts]

    return _fake_embed_batch


async def _fake_stream_generation(_messages: list[dict]):
    yield "Mitochondria power the cell. [1]"


@pytest_asyncio.fixture
async def seeded(postgres_container: PostgresContainer):
    """One course, one book/chunk, one enrolled student."""
    async with async_session_factory() as session:
        owner = await _make_user(session, "retrieval-prof@sfu.ca")
        course = await _make_course(session, owner, "BISC 150")
        book = await _make_book(session, course, owner, "d" * 64)
        await session.flush()

        await replace_chunks_for_book(
            session,
            book.id,
            [
                {
                    "chunk_index": 0,
                    "text": MITOCHONDRIA_TEXT,
                    "page_start": 4,
                    "page_end": 5,
                    "section_path": ["Chapter 1", "Cell Biology"],
                    "token_count": 8,
                }
            ],
            [_unit_vector(1024, 0)],
        )

        student = await _make_user(session, "retrieval-student@sfu.ca", Role.student)
        session.add(Enrollment(user_id=student.id, course_id=course.id))

        await session.commit()

        return {"course_id": course.id, "book_id": book.id}


async def _get_conversation_id(async_client: AsyncClient, course_id: uuid.UUID) -> uuid.UUID:
    response = await async_client.get(f"/courses/{course_id}/chat/conversation")
    assert response.status_code == 200
    return uuid.UUID(response.json()["conversation_id"])


async def _post_message(
    async_client: AsyncClient, course_id: uuid.UUID, conversation_id: uuid.UUID, content: str
):
    return await async_client.post(
        f"/courses/{course_id}/chat/conversations/{conversation_id}/messages",
        json={"content": content},
        headers=CSRF_HEADERS,
    )


def _parse_sse(text: str) -> list[tuple[str | None, Any]]:
    events: list[tuple[str | None, Any]] = []
    for block in text.strip("\n").split("\n\n"):
        if not block.strip():
            continue
        event_name = None
        data_lines = []
        for line in block.splitlines():
            if line.startswith("event:"):
                event_name = line[len("event:") :].strip()
            elif line.startswith("data:"):
                data_lines.append(line[len("data:") :].strip())
        data_raw = "\n".join(data_lines)
        data = json.loads(data_raw) if data_raw else None
        events.append((event_name, data))
    return events


async def test_retrieval_step_exception_surfaces_as_sse_error_event(
    async_client: AsyncClient,
    postgres_container: PostgresContainer,
    seeded: dict,
    monkeypatch: pytest.MonkeyPatch,
    caplog,
) -> None:
    """When an exception is raised during the retrieval step (embed_batch,
    vector_arm, keyword_arm, or fuse_rrf), it must surface as a graceful SSE
    `error` event with a plain-language message - NOT an unhandled exception
    that breaks the stream or produces a 500.

    This is the core regression for CR-01: the fix in commit 94f80c0 broadened
    the exception handler from catching only LLMCondenseError to catching all
    Exception types around the entire retrieval pipeline (lines 134-152 in
    routes.py).
    """
    await _login(async_client, "retrieval-student", "student")
    conversation_id = await _get_conversation_id(async_client, seeded["course_id"])

    # Monkeypatch vector_arm to raise a generic exception mid-retrieval
    async def _failing_vector_arm(*args, **kwargs):
        raise RuntimeError("Simulated vector search database failure")

    embedded_texts: list[str] = []
    monkeypatch.setattr("app.chat.routes.embed_batch", _fake_embed_batch_factory(embedded_texts))
    monkeypatch.setattr("app.search.repo.vector_arm", _failing_vector_arm)
    monkeypatch.setattr("app.chat.routes.stream_generation", _fake_stream_generation)

    response = await _post_message(
        async_client, seeded["course_id"], conversation_id, "What powers the cell?"
    )

    # Response should still be 200 (the streaming connection succeeded)
    assert response.status_code == 200

    # Parse the SSE frames from the response
    events = _parse_sse(response.text)
    event_types = [event for event, _ in events]

    # Must contain an error event, not a done event
    assert "error" in event_types, f"Expected 'error' event, got: {event_types}"
    assert "done" not in event_types, "After a retrieval failure, no done event should be emitted"

    # The error event should contain the plain-language message
    error_data = [data for event, data in events if event == "error"]
    assert len(error_data) >= 1, "Expected at least one error event"
    assert "Something went wrong preparing your question" in error_data[0]

    # No citations or generation should happen after retrieval failure
    citations_events = [data for event, data in events if event == "citations"]
    assert len(citations_events) == 0, "No citations should be emitted on retrieval failure"

    # The exception should have been logged (logger.exception was called)
    assert any(
        "retrieval failed" in record.message.lower()
        or "query condensation" in record.message.lower()
        for record in caplog.records
        if record.levelname == "ERROR"
    )
