"""D9 (streaming event vocabulary, CHAT-02): the SSE response uses the
app's own event vocabulary (`token` / `citations` / `done`, `error` on
failure) - never the upstream OpenAI-compatible wire shape (`data: {...}`
frames with a `choices`/`delta` envelope) proxied verbatim to the browser.
The first `token` event must be produced before the stream completes.
"""

import json
import uuid

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

_ALLOWED_EVENTS = {"token", "citations", "done", "error"}


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
async def seeded(postgres_container: PostgresContainer):
    async with async_session_factory() as session:
        owner = await _make_user(session, "streamvocab-prof@sfu.ca")
        course = await _make_course(session, owner, "BISC 240")
        book = await _make_book(session, course, owner, "2" * 64)
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

        student = await _make_user(session, "streamvocab-student@sfu.ca", Role.student)
        session.add(Enrollment(user_id=student.id, course_id=course.id))

        await session.commit()

        return {"course_id": course.id}


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


async def test_streaming_uses_app_event_vocabulary_not_raw_upstream_frames(
    async_client: AsyncClient,
    postgres_container: PostgresContainer,
    seeded: dict,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _fake_stream_generation(_messages):
        yield "Mitochondria power the cell. "
        yield "[1]"

    monkeypatch.setattr("app.chat.routes.stream_generation", _fake_stream_generation)

    await _login(async_client, "streamvocab-student", "student")
    conversation_id = await _get_conversation_id(async_client, seeded["course_id"])

    response = await _post_message(
        async_client, seeded["course_id"], conversation_id, "What powers the cell?"
    )
    assert response.status_code == 200

    events = _parse_sse(response.text)
    event_names = [event for event, _ in events]

    assert event_names, "expected at least one SSE event"
    assert set(event_names) <= _ALLOWED_EVENTS
    assert "token" in event_names

    # The first `token` event must arrive strictly before `done` - i.e. some
    # generation output was already produced before the stream completes.
    assert event_names.index("token") < event_names.index("done")

    # The wire shape is the app's own vocabulary, never the raw upstream
    # OpenAI-compatible SSE frame (`data: {"choices": [...], ...}`) that
    # `llm_client.stream_generation` itself consumes internally.
    assert '"choices"' not in response.text
    assert '"delta"' not in response.text


async def test_generation_failure_emits_error_event_in_app_vocabulary(
    async_client: AsyncClient,
    postgres_container: PostgresContainer,
    seeded: dict,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _failing_stream_generation(_messages):
        yield "partial "
        raise RuntimeError("simulated upstream failure mid-stream")

    monkeypatch.setattr("app.chat.routes.stream_generation", _failing_stream_generation)

    await _login(async_client, "streamvocab-student", "student")
    conversation_id = await _get_conversation_id(async_client, seeded["course_id"])

    response = await _post_message(
        async_client, seeded["course_id"], conversation_id, "What powers the cell?"
    )
    assert response.status_code == 200

    events = _parse_sse(response.text)
    event_names = [event for event, _ in events]

    assert set(event_names) <= _ALLOWED_EVENTS
    assert "error" in event_names
    assert "done" not in event_names
    assert "RuntimeError" not in response.text  # no raw exception dump leaks to the client
