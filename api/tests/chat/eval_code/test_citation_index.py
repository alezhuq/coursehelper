"""Citation-index validity: every `[n]` marker in a
produced answer must resolve to `n` in `range(1, len(sources)+1)` - a marker
citing an index the retrieval step never provided is dropped and logged at
WARNING, never rendered to the student; every marker within range resolves
to its correct denormalized citation (book/page/section).
"""

import json
import logging
import uuid

import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy import select
from testcontainers.postgres import PostgresContainer

from app.books.models import Book, BookStatus
from app.chunks.models import Chunk
from app.chunks.repo import replace_chunks_for_book
from app.courses.models import Course
from app.db.base import async_session_factory
from app.enrollments.models import Enrollment
from app.users.models import Role, User
from app.users.repo import upsert_user

CSRF_HEADERS = {"X-Requested-With": "fetch"}


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


@pytest_asyncio.fixture(autouse=True)
def _mock_embed_batch(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "app.chat.routes.embed_batch", lambda texts: [_unit_vector(1024, 0) for _ in texts]
    )


@pytest_asyncio.fixture
async def one_chunk_seeded(postgres_container: PostgresContainer):
    """A single retrieved chunk - `sources` will always have length 1, so
    any marker other than `[1]` is definitionally hallucinated."""
    async with async_session_factory() as session:
        owner = await _make_user(session, "citindex-prof@sfu.ca")
        course = await _make_course(session, owner, "BISC 210")
        book = await _make_book(session, course, owner, "e" * 64)
        await session.flush()

        await replace_chunks_for_book(
            session,
            book.id,
            [
                {
                    "chunk_index": 0,
                    "text": "mitochondria are the powerhouse of the cell",
                    "page_start": 4,
                    "page_end": 5,
                    "section_path": ["Chapter 1", "Cell Biology"],
                    "token_count": 8,
                }
            ],
            [_unit_vector(1024, 0)],
        )

        student = await _make_user(session, "citindex-student@sfu.ca", Role.student)
        session.add(Enrollment(user_id=student.id, course_id=course.id))

        await session.commit()

        return {"course_id": course.id, "book_id": book.id}


@pytest_asyncio.fixture
async def two_chunk_seeded(postgres_container: PostgresContainer):
    """Two retrieved chunks with a deterministic fused order - RRF's own
    ranking math is the search layer's concern, not this test's, so `fuse_rrf` is
    monkeypatched to a fixed order and `[1]`/`[2]` map predictably."""
    async with async_session_factory() as session:
        owner = await _make_user(session, "citindex2-prof@sfu.ca")
        course = await _make_course(session, owner, "BISC 211")
        book = await _make_book(session, course, owner, "f" * 64)
        await session.flush()

        await replace_chunks_for_book(
            session,
            book.id,
            [
                {
                    "chunk_index": 0,
                    "text": "mitochondria produce atp via oxidative phosphorylation",
                    "page_start": 4,
                    "page_end": 5,
                    "section_path": ["Chapter 1", "Cell Biology"],
                    "token_count": 8,
                },
                {
                    "chunk_index": 1,
                    "text": "the krebs cycle produces atp through oxidation",
                    "page_start": 6,
                    "page_end": 7,
                    "section_path": ["Chapter 1", "Metabolism"],
                    "token_count": 8,
                },
            ],
            [_unit_vector(1024, 0), _unit_vector(1024, 1)],
        )

        student = await _make_user(session, "citindex2-student@sfu.ca", Role.student)
        session.add(Enrollment(user_id=student.id, course_id=course.id))

        await session.commit()

        result = await session.execute(
            select(Chunk.id).where(Chunk.book_id == book.id).order_by(Chunk.chunk_index)
        )
        chunk_ids = [str(row) for row in result.scalars().all()]

        return {"course_id": course.id, "book_id": book.id, "chunk_ids": chunk_ids}


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


def _parse_sse(text: str):
    events = []
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


async def test_hallucinated_citation_index_is_dropped_and_logged(
    async_client: AsyncClient,
    postgres_container: PostgresContainer,
    one_chunk_seeded: dict,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    async def _hallucinating_stream_generation(_messages):
        yield "This claims something not actually supported. [3]"

    monkeypatch.setattr("app.chat.routes.stream_generation", _hallucinating_stream_generation)

    await _login(async_client, "citindex-student", "student")
    conversation_id = await _get_conversation_id(async_client, one_chunk_seeded["course_id"])

    with caplog.at_level(logging.WARNING, logger="app.chat.citations"):
        response = await _post_message(
            async_client, one_chunk_seeded["course_id"], conversation_id, "What powers the cell?"
        )
    assert response.status_code == 200

    events = _parse_sse(response.text)
    citation_events = [data for event, data in events if event == "citations"]
    assert len(citation_events) == 1
    assert citation_events[0] == []  # marker 3 dropped - only 1 source was retrieved

    assert "hallucinated citation index 3" in caplog.text


async def test_valid_citation_indices_resolve(
    async_client: AsyncClient,
    postgres_container: PostgresContainer,
    two_chunk_seeded: dict,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    chunk_ids = two_chunk_seeded["chunk_ids"]
    monkeypatch.setattr("app.chat.routes.fuse_rrf", lambda vector_ids, keyword_ids: chunk_ids)

    async def _dual_citation_stream_generation(_messages):
        yield "Mitochondria produce ATP [1], and so does the krebs cycle [2]."

    monkeypatch.setattr("app.chat.routes.stream_generation", _dual_citation_stream_generation)

    await _login(async_client, "citindex2-student", "student")
    conversation_id = await _get_conversation_id(async_client, two_chunk_seeded["course_id"])

    response = await _post_message(
        async_client, two_chunk_seeded["course_id"], conversation_id, "How is ATP produced?"
    )
    assert response.status_code == 200

    events = _parse_sse(response.text)
    citation_events = [data for event, data in events if event == "citations"]
    assert len(citation_events) == 1
    citations = citation_events[0]
    assert len(citations) == 2
    assert citations[0]["marker"] == 1
    assert citations[0]["page_start"] == 4
    assert citations[1]["marker"] == 2
    assert citations[1]["page_start"] == 6
