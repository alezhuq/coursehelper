"""D6 (course isolation, AUTH-05): a question whose only answer lives in
another course refuses (never leaks that course's chunk/citation) when
asked inside a course with no relevant material, and a student who isn't
enrolled in the target course is denied outright.
"""

import json
import uuid

import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy import select
from testcontainers.postgres import PostgresContainer

from app.books.models import Book, BookStatus
from app.chat.models import Message
from app.chat.prompts import REFUSAL_MESSAGE
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
async def seeded(postgres_container: PostgresContainer):
    """Course A: an enrolled student, zero indexed chunks. Course B: a
    different professor's course holding the ONLY chunk that answers the
    question asked below - the student is never enrolled in it."""
    async with async_session_factory() as session:
        owner_a = await _make_user(session, "courseiso-prof-a@sfu.ca")
        course_a = await _make_course(session, owner_a, "BISC 230")

        owner_b = await _make_user(session, "courseiso-prof-b@sfu.ca")
        course_b = await _make_course(session, owner_b, "BISC 231")
        book_b = await _make_book(session, course_b, owner_b, "1" * 64)
        await session.flush()

        await replace_chunks_for_book(
            session,
            book_b.id,
            [
                {
                    "chunk_index": 0,
                    "text": "the krebs cycle produces atp through oxidation",
                    "page_start": 1,
                    "page_end": 1,
                    "section_path": [],
                    "token_count": 8,
                }
            ],
            [_unit_vector(1024, 0)],
        )

        student = await _make_user(session, "courseiso-student@sfu.ca", Role.student)
        session.add(Enrollment(user_id=student.id, course_id=course_a.id))
        # student is NOT enrolled in course_b.

        await session.commit()

        return {"course_a_id": course_a.id, "course_b_id": course_b.id, "book_b_id": book_b.id}


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


async def test_cross_course_question_refuses_and_never_leaks_other_course_chunk(
    async_client: AsyncClient,
    postgres_container: PostgresContainer,
    seeded: dict,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _stream_generation_must_not_be_called(*_args, **_kwargs):
        raise AssertionError(
            "stream_generation must not be called - course A has no chunks; "
            "course B's krebs-cycle chunk must never be retrieved from course A"
        )

    monkeypatch.setattr("app.chat.routes.stream_generation", _stream_generation_must_not_be_called)

    await _login(async_client, "courseiso-student", "student")
    conversation_id = await _get_conversation_id(async_client, seeded["course_a_id"])

    response = await _post_message(
        async_client, seeded["course_a_id"], conversation_id, "Explain the krebs cycle"
    )
    assert response.status_code == 200

    events = _parse_sse(response.text)
    token_events = [data for event, data in events if event == "token"]
    citation_events = [data for event, data in events if event == "citations"]

    assert token_events == [REFUSAL_MESSAGE]
    assert citation_events == []

    async with async_session_factory() as session:
        result = await session.execute(
            select(Message)
            .where(Message.conversation_id == conversation_id)
            .order_by(Message.created_at.asc())
        )
        messages = list(result.scalars().all())

    assert len(messages) == 2
    assistant_message = messages[1]
    assert assistant_message.content == REFUSAL_MESSAGE
    assert assistant_message.citations == []
    assert str(seeded["book_b_id"]) not in assistant_message.content


async def test_non_enrolled_student_denied_403_for_target_course(
    async_client: AsyncClient,
    postgres_container: PostgresContainer,
    seeded: dict,
) -> None:
    await _login(async_client, "courseiso-student", "student")

    response = await async_client.get(f"/courses/{seeded['course_b_id']}/chat/conversation")
    assert response.status_code == 403
