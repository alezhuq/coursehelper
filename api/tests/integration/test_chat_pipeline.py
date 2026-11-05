"""Hermetic, stub-LLM end-to-end integration test for the chat pipeline - the tracer's proof.

Real Postgres (via `postgres_container`), real retrieval/persistence, real
RRF fusion - only the LLM calls are stubbed (`app.chat.routes.
condense_query` / `app.chat.routes.stream_generation`, monkeypatched at
their call-site import names exactly like `app.search.routes.embed_batch`
in `test_search_hybrid.py`), so this suite needs no live Ollama.
"""

import asyncio
import json
import logging
import re
import uuid
from typing import Any

import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy import select, text
from testcontainers.postgres import PostgresContainer

from app.books.models import Book, BookStatus
from app.chat import repo as chat_repo
from app.chat.models import Message
from app.chunks.repo import replace_chunks_for_book
from app.courses.models import Course
from app.db.base import async_session_factory
from app.enrollments.models import Enrollment
from app.settings import get_settings
from app.users.models import Role, User
from app.users.repo import upsert_user
from tests.race import wait_for_lock_wait

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


def _fake_embed_batch(texts: list[str]) -> list[list[float]]:
    return [_unit_vector(1024, 0) for _ in texts]


@pytest_asyncio.fixture(autouse=True)
def _mock_embed_batch(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.chat.routes.embed_batch", _fake_embed_batch)


@pytest_asyncio.fixture
async def seeded(postgres_container: PostgresContainer):
    """Course A: one book, one chunk, an enrolled student - retrieval
    always returns exactly that one chunk as the sole candidate (it's the
    only row scoped to the course, so vector_arm returns it regardless of
    the stubbed embedding value). Course Empty: enrolled, zero chunks -
    retrieval always returns nothing, exercising the gate."""
    async with async_session_factory() as session:
        owner = await _make_user(session, "chat-prof@sfu.ca")
        course_a = await _make_course(session, owner, "BISC 100")
        book_a = await _make_book(session, course_a, owner, "c" * 64)
        await session.flush()

        await replace_chunks_for_book(
            session,
            book_a.id,
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

        course_empty = await _make_course(session, owner, "BISC 200")

        student = await _make_user(session, "chat-student@sfu.ca", Role.student)
        session.add(Enrollment(user_id=student.id, course_id=course_a.id))
        session.add(Enrollment(user_id=student.id, course_id=course_empty.id))

        other_owner = await _make_user(session, "chat-prof-other@sfu.ca")
        course_other = await _make_course(session, other_owner, "BISC 300")
        # student is NOT enrolled in course_other.

        await session.commit()

        return {
            "course_a_id": course_a.id,
            "course_empty_id": course_empty.id,
            "course_other_id": course_other.id,
            "book_a_id": book_a.id,
        }


def _parse_sse(text: str) -> list[tuple[str | None, Any]]:
    """Parse an `EventSourceResponse` body into (event, data) pairs. `data`
    is JSON-decoded (ServerSentEvent always JSON-serializes its `data`
    field, even plain strings) - a `done` event carries no data field at
    all, so its `data` here is `None`."""
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


async def _fake_stream_generation(_messages: list[dict]):
    for delta in ["Mitochondria are the cell's energy powerhouse. ", "[1]"]:
        yield delta


@pytest_asyncio.fixture
def _mock_stream_generation(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr("app.chat.routes.stream_generation", _fake_stream_generation)


async def test_first_turn_happy_path_streams_answer_citations_and_persists(
    async_client: AsyncClient,
    postgres_container: PostgresContainer,
    seeded: dict,
    _mock_stream_generation: None,
) -> None:
    await _login(async_client, "chat-student", "student")
    conversation_id = await _get_conversation_id(async_client, seeded["course_a_id"])

    response = await _post_message(
        async_client, seeded["course_a_id"], conversation_id, "What powers the cell?"
    )
    assert response.status_code == 200

    events = _parse_sse(response.text)
    token_events = [data for event, data in events if event == "token"]
    citation_events = [data for event, data in events if event == "citations"]
    done_events = [event for event, _ in events if event == "done"]

    assert token_events, "expected at least one token event"
    full_answer = "".join(token_events)
    assert full_answer == "Mitochondria are the cell's energy powerhouse. [1]"

    assert len(citation_events) == 1
    citations = citation_events[0]
    assert len(citations) == 1
    assert citations[0]["marker"] == 1
    assert citations[0]["book_id"] == str(seeded["book_a_id"])
    assert citations[0]["page_start"] == 4
    assert citations[0]["page_end"] == 5

    assert done_events == ["done"]

    async with async_session_factory() as session:
        result = await session.execute(
            select(Message)
            .where(Message.conversation_id == conversation_id)
            .order_by(Message.created_at.asc())
        )
        messages = list(result.scalars().all())

    assert len(messages) == 2
    assert messages[0].role == "user"
    assert messages[0].content == "What powers the cell?"
    assert messages[1].role == "assistant"
    assert messages[1].content == full_answer
    assert len(messages[1].citations) == 1
    assert messages[1].citations[0]["marker"] == 1
    assert messages[1].citations[0]["book_id"] == str(seeded["book_a_id"])


async def test_empty_retrieval_refuses_with_no_generation_call(
    async_client: AsyncClient,
    postgres_container: PostgresContainer,
    seeded: dict,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    called = {"value": False}

    def _spy_stream_generation(*_args, **_kwargs):
        called["value"] = True
        raise AssertionError("stream_generation must not be called on empty retrieval")

    monkeypatch.setattr("app.chat.routes.stream_generation", _spy_stream_generation)

    await _login(async_client, "chat-student", "student")
    conversation_id = await _get_conversation_id(async_client, seeded["course_empty_id"])

    response = await _post_message(
        async_client, seeded["course_empty_id"], conversation_id, "Explain photosynthesis"
    )
    assert response.status_code == 200

    events = _parse_sse(response.text)
    token_events = [data for event, data in events if event == "token"]
    done_events = [event for event, _ in events if event == "done"]
    citation_events = [event for event, _ in events if event == "citations"]

    assert token_events == ["The course materials don't cover this question."]
    assert done_events == ["done"]
    assert citation_events == []
    assert called["value"] is False

    async with async_session_factory() as session:
        result = await session.execute(
            select(Message)
            .where(Message.conversation_id == conversation_id)
            .order_by(Message.created_at.asc())
        )
        messages = list(result.scalars().all())

    assert len(messages) == 2
    assert messages[1].role == "assistant"
    assert messages[1].content == "The course materials don't cover this question."
    assert messages[1].citations == []


async def test_hallucinated_citation_index_is_dropped_and_logged(
    async_client: AsyncClient,
    postgres_container: PostgresContainer,
    seeded: dict,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    async def _hallucinating_stream_generation(_messages: list[dict]):
        yield "This claims something not actually supported. [3]"

    monkeypatch.setattr("app.chat.routes.stream_generation", _hallucinating_stream_generation)

    await _login(async_client, "chat-student", "student")
    conversation_id = await _get_conversation_id(async_client, seeded["course_a_id"])

    with caplog.at_level(logging.WARNING, logger="app.chat.citations"):
        response = await _post_message(
            async_client, seeded["course_a_id"], conversation_id, "What powers the cell?"
        )
    assert response.status_code == 200

    events = _parse_sse(response.text)
    citation_events = [data for event, data in events if event == "citations"]
    assert len(citation_events) == 1
    assert citation_events[0] == []  # marker 3 dropped - only 1 source was retrieved

    assert "hallucinated citation index 3" in caplog.text


async def test_mid_stream_interruption_persists_no_assistant_row(
    async_client: AsyncClient,
    postgres_container: PostgresContainer,
    seeded: dict,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _failing_stream_generation(_messages: list[dict]):
        yield "partial answer that never finishes "
        raise RuntimeError("simulated upstream failure mid-stream")

    monkeypatch.setattr("app.chat.routes.stream_generation", _failing_stream_generation)

    await _login(async_client, "chat-student", "student")
    conversation_id = await _get_conversation_id(async_client, seeded["course_a_id"])

    response = await _post_message(
        async_client, seeded["course_a_id"], conversation_id, "What powers the cell?"
    )
    assert response.status_code == 200

    events = _parse_sse(response.text)
    event_names = [event for event, _ in events]
    assert "error" in event_names
    assert "done" not in event_names

    async with async_session_factory() as session:
        result = await session.execute(
            select(Message).where(Message.conversation_id == conversation_id)
        )
        messages = list(result.scalars().all())

    assert len(messages) == 1
    assert messages[0].role == "user"


async def test_non_enrolled_student_denied_403(
    async_client: AsyncClient,
    postgres_container: PostgresContainer,
    seeded: dict,
) -> None:
    await _login(async_client, "chat-student", "student")

    response = await async_client.get(f"/courses/{seeded['course_other_id']}/chat/conversation")
    assert response.status_code == 403


async def test_foreign_conversation_id_404s(
    async_client: AsyncClient,
    postgres_container: PostgresContainer,
    seeded: dict,
) -> None:
    await _login(async_client, "chat-student", "student")

    # A real conversation_id that belongs to course_a, posted through
    # course_empty's path - must 404 (ownership re-derivation),
    # never load or persist against the wrong course.
    conversation_id = await _get_conversation_id(async_client, seeded["course_a_id"])

    response = await _post_message(
        async_client, seeded["course_empty_id"], conversation_id, "What powers the cell?"
    )
    assert response.status_code == 404


async def _clear_conversation(async_client: AsyncClient, course_id: uuid.UUID):
    return await async_client.delete(
        f"/courses/{course_id}/chat/conversation", headers=CSRF_HEADERS
    )


async def test_clear_conversation_deletes_messages_keeps_conversation_id(
    async_client: AsyncClient,
    postgres_container: PostgresContainer,
    seeded: dict,
    _mock_stream_generation: None,
) -> None:
    await _login(async_client, "chat-student", "student")
    conversation_id = await _get_conversation_id(async_client, seeded["course_a_id"])

    response = await _post_message(
        async_client, seeded["course_a_id"], conversation_id, "What powers the cell?"
    )
    assert response.status_code == 200

    clear_response = await _clear_conversation(async_client, seeded["course_a_id"])
    assert clear_response.status_code == 204

    follow_up_id = await _get_conversation_id(async_client, seeded["course_a_id"])
    assert follow_up_id == conversation_id

    get_response = await async_client.get(f"/courses/{seeded['course_a_id']}/chat/conversation")
    assert get_response.status_code == 200
    assert get_response.json()["messages"] == []

    async with async_session_factory() as session:
        result = await session.execute(
            select(Message).where(Message.conversation_id == conversation_id)
        )
        messages = list(result.scalars().all())
    assert messages == []


async def test_clear_conversation_with_no_messages_returns_204(
    async_client: AsyncClient,
    postgres_container: PostgresContainer,
    seeded: dict,
) -> None:
    await _login(async_client, "chat-student", "student")

    response = await _clear_conversation(async_client, seeded["course_empty_id"])
    assert response.status_code == 204

    # Safe to call repeatedly.
    response = await _clear_conversation(async_client, seeded["course_empty_id"])
    assert response.status_code == 204


async def test_clear_conversation_then_new_message_only_contains_new_turn(
    async_client: AsyncClient,
    postgres_container: PostgresContainer,
    seeded: dict,
    _mock_stream_generation: None,
) -> None:
    await _login(async_client, "chat-student", "student")
    conversation_id = await _get_conversation_id(async_client, seeded["course_a_id"])

    await _post_message(
        async_client, seeded["course_a_id"], conversation_id, "What powers the cell?"
    )

    clear_response = await _clear_conversation(async_client, seeded["course_a_id"])
    assert clear_response.status_code == 204

    response = await _post_message(
        async_client, seeded["course_a_id"], conversation_id, "What is DNA?"
    )
    assert response.status_code == 200

    async with async_session_factory() as session:
        result = await session.execute(
            select(Message)
            .where(Message.conversation_id == conversation_id)
            .order_by(Message.created_at.asc())
        )
        messages = list(result.scalars().all())

    assert len(messages) == 2
    assert messages[0].role == "user"
    assert messages[0].content == "What is DNA?"


async def test_clear_conversation_missing_csrf_header_rejected(
    async_client: AsyncClient,
    postgres_container: PostgresContainer,
    seeded: dict,
    _mock_stream_generation: None,
) -> None:
    await _login(async_client, "chat-student", "student")
    conversation_id = await _get_conversation_id(async_client, seeded["course_a_id"])

    await _post_message(
        async_client, seeded["course_a_id"], conversation_id, "What powers the cell?"
    )

    response = await async_client.delete(f"/courses/{seeded['course_a_id']}/chat/conversation")
    assert response.status_code == 403

    async with async_session_factory() as session:
        result = await session.execute(
            select(Message).where(Message.conversation_id == conversation_id)
        )
        messages = list(result.scalars().all())
    assert len(messages) == 2


async def test_clear_conversation_only_clears_the_calling_users_own(
    async_client: AsyncClient,
    postgres_container: PostgresContainer,
    seeded: dict,
    _mock_stream_generation: None,
) -> None:
    async with async_session_factory() as session:
        second_student = await _make_user(session, "chat-student-2@sfu.ca", Role.student)
        session.add(Enrollment(user_id=second_student.id, course_id=seeded["course_a_id"]))
        await session.commit()

    await _login(async_client, "chat-student", "student")
    first_conversation_id = await _get_conversation_id(async_client, seeded["course_a_id"])
    await _post_message(
        async_client, seeded["course_a_id"], first_conversation_id, "What powers the cell?"
    )

    await _login(async_client, "chat-student-2", "student")
    second_conversation_id = await _get_conversation_id(async_client, seeded["course_a_id"])
    assert second_conversation_id != first_conversation_id

    clear_response = await _clear_conversation(async_client, seeded["course_a_id"])
    assert clear_response.status_code == 204

    async with async_session_factory() as session:
        result = await session.execute(
            select(Message).where(Message.conversation_id == first_conversation_id)
        )
        first_user_messages = list(result.scalars().all())
    assert len(first_user_messages) == 2


async def test_clear_conversation_non_enrolled_student_denied_403(
    async_client: AsyncClient,
    postgres_container: PostgresContainer,
    seeded: dict,
) -> None:
    await _login(async_client, "chat-student", "student")

    response = await _clear_conversation(async_client, seeded["course_other_id"])
    assert response.status_code == 403


# --- Regression tests: source budget, race safety, stream errors ------------

BUDGET_TEXTS = [
    "Alpha section: glycolysis converts glucose into pyruvate in the cytosol.",
    "Beta section: the citric acid cycle oxidises acetyl CoA in the matrix.",
    "Gamma section: oxidative phosphorylation produces most of the ATP.",
]


def _mixed_vector(dim: int, weight: float) -> list[float]:
    vec = [0.0] * dim
    vec[0] = 1.0
    vec[1] = weight
    return vec


@pytest_asyncio.fixture
async def budget_seeded(postgres_container: PostgresContainer):
    """One course with three 600-token chunks whose vectors rank them
    deterministically (chunk 0 closest to the stubbed query embedding)."""
    async with async_session_factory() as session:
        owner = await _make_user(session, "budget-prof@sfu.ca")
        course = await _make_course(session, owner, "BISC 400")
        book = await _make_book(session, course, owner, "d" * 64)
        await session.flush()
        await replace_chunks_for_book(
            session,
            book.id,
            [
                {
                    "chunk_index": i,
                    "text": BUDGET_TEXTS[i],
                    "page_start": 10 + i,
                    "page_end": 10 + i,
                    "section_path": ["Chapter 2"],
                    "token_count": 600,
                }
                for i in range(3)
            ],
            [_mixed_vector(1024, 0.5 * i) for i in range(3)],
        )
        student = await _make_user(session, "budget-student@sfu.ca", Role.student)
        session.add(Enrollment(user_id=student.id, course_id=course.id))
        await session.commit()
        return {"course_id": course.id, "book_id": book.id}


@pytest.fixture
def small_source_budget(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("LLM_SOURCE_TOKEN_BUDGET", "1300")
    get_settings.cache_clear()
    yield
    monkeypatch.delenv("LLM_SOURCE_TOKEN_BUDGET")
    get_settings.cache_clear()


async def _enrolled_student(course_id: uuid.UUID, name: str) -> None:
    async with async_session_factory() as session:
        student = await _make_user(session, f"{name}@sfu.ca", Role.student)
        session.add(Enrollment(user_id=student.id, course_id=course_id))
        await session.commit()


async def test_prompt_uses_full_chunk_text_within_token_budget_and_citations_match(
    async_client: AsyncClient,
    postgres_container: PostgresContainer,
    budget_seeded: dict,
    small_source_budget: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: list[list[dict]] = []

    async def _stream(messages: list[dict]):
        captured.append(messages)
        yield "First [1]. Third [3]."

    monkeypatch.setattr("app.chat.routes.stream_generation", _stream)

    await _login(async_client, "budget-student", "student")
    conversation_id = await _get_conversation_id(async_client, budget_seeded["course_id"])
    response = await _post_message(
        async_client, budget_seeded["course_id"], conversation_id, "Zzyzx quuxlet"
    )
    assert response.status_code == 200

    user_message = captured[0][-1]["content"]
    blocks = re.findall(r'<source id="(\d+)">(.*?)</source>', user_message, flags=re.DOTALL)
    # 3 x 600 tokens with a 1300 budget: two whole chunks, never a partial.
    assert [(int(i), body) for i, body in blocks] == [(1, BUDGET_TEXTS[0]), (2, BUDGET_TEXTS[1])]

    events = _parse_sse(response.text)
    citation_events = [data for event, data in events if event == "citations"]
    assert len(citation_events) == 1
    assert [c["marker"] for c in citation_events[0]] == [1]

    async with async_session_factory() as session:
        assistant = (
            await session.execute(
                select(Message).where(
                    Message.conversation_id == conversation_id, Message.role == "assistant"
                )
            )
        ).scalar_one()
    assert [c["marker"] for c in assistant.citations] == [1]


async def test_no_transaction_is_held_open_while_the_llm_streams(
    async_client: AsyncClient,
    postgres_container: PostgresContainer,
    seeded: dict,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    idle_in_tx: list[int] = []

    async def _stream(_messages: list[dict]):
        async with async_session_factory() as probe:
            count = (
                await probe.execute(
                    text(
                        "SELECT count(*) FROM pg_stat_activity "
                        "WHERE datname = current_database() "
                        "AND pid <> pg_backend_pid() "
                        "AND state LIKE 'idle in transaction%'"
                    )
                )
            ).scalar_one()
            await probe.rollback()
        idle_in_tx.append(count)
        yield "Answer. [1]"

    monkeypatch.setattr("app.chat.routes.stream_generation", _stream)

    await _enrolled_student(seeded["course_a_id"], "chat-tx-student")
    await _login(async_client, "chat-tx-student", "student")
    conversation_id = await _get_conversation_id(async_client, seeded["course_a_id"])
    response = await _post_message(
        async_client, seeded["course_a_id"], conversation_id, "What powers the cell?"
    )
    assert response.status_code == 200
    assert idle_in_tx == [0]


async def test_try_again_reuses_the_unanswered_user_turn(
    async_client: AsyncClient,
    postgres_container: PostgresContainer,
    seeded: dict,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    question = "What powers the cell?"
    condense_calls: list[str] = []
    captured: list[list[dict]] = []

    async def _failing_stream(_messages: list[dict]):
        yield "partial "
        raise RuntimeError("simulated upstream failure")

    async def _capturing_stream(messages: list[dict]):
        captured.append(messages)
        yield "Mitochondria. [1]"

    async def _condense(history: list[dict], content: str) -> str:
        condense_calls.append(content)
        return content

    monkeypatch.setattr("app.chat.routes.condense_query", _condense)
    await _enrolled_student(seeded["course_a_id"], "chat-retry-student")
    await _login(async_client, "chat-retry-student", "student")
    conversation_id = await _get_conversation_id(async_client, seeded["course_a_id"])

    monkeypatch.setattr("app.chat.routes.stream_generation", _failing_stream)
    first = await _post_message(async_client, seeded["course_a_id"], conversation_id, question)
    assert "error" in [event for event, _ in _parse_sse(first.text)]

    monkeypatch.setattr("app.chat.routes.stream_generation", _capturing_stream)
    second = await _post_message(async_client, seeded["course_a_id"], conversation_id, question)
    assert "done" in [event for event, _ in _parse_sse(second.text)]

    async with async_session_factory() as session:
        rows = list(
            (
                await session.execute(
                    select(Message)
                    .where(Message.conversation_id == conversation_id)
                    .order_by(Message.created_at.asc())
                )
            )
            .scalars()
            .all()
        )
    assert [(m.role, m.content) for m in rows if m.role == "user"] == [("user", question)]
    assert len([m for m in rows if m.role == "assistant"]) == 1

    # The retried prompt carries the question exactly once: no stale copy in
    # history and no condensation call on a first-turn retry.
    assert condense_calls == []
    all_text = "\n".join(m["content"] for m in captured[0])
    assert all_text.count(question) == 1
    assert [m["role"] for m in captured[0]] == ["system", "user"]


async def test_identical_question_after_an_answered_turn_is_a_new_turn(
    async_client: AsyncClient,
    postgres_container: PostgresContainer,
    seeded: dict,
    _mock_stream_generation: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _condense(history: list[dict], content: str) -> str:
        return content

    monkeypatch.setattr("app.chat.routes.condense_query", _condense)
    await _enrolled_student(seeded["course_a_id"], "chat-twice-student")
    await _login(async_client, "chat-twice-student", "student")
    conversation_id = await _get_conversation_id(async_client, seeded["course_a_id"])

    for _ in range(2):
        response = await _post_message(
            async_client, seeded["course_a_id"], conversation_id, "What powers the cell?"
        )
        assert response.status_code == 200

    async with async_session_factory() as session:
        rows = list(
            (
                await session.execute(
                    select(Message).where(Message.conversation_id == conversation_id)
                )
            )
            .scalars()
            .all()
        )
    assert len([m for m in rows if m.role == "user"]) == 2
    assert len([m for m in rows if m.role == "assistant"]) == 2


async def test_get_or_create_conversation_is_race_safe(
    postgres_container: PostgresContainer, seeded: dict
) -> None:
    async with async_session_factory() as setup:
        user = await _make_user(setup, "chat-race-user@sfu.ca", Role.student)
        await setup.commit()
        user_id = user.id

    course_id = seeded["course_a_id"]
    async with async_session_factory() as session_a, async_session_factory() as session_b:
        conversation_a = await chat_repo.get_or_create_conversation(session_a, user_id, course_id)
        task_b = asyncio.create_task(
            chat_repo.get_or_create_conversation(session_b, user_id, course_id)
        )
        try:
            await wait_for_lock_wait(task_b)
        finally:
            await session_a.commit()
        conversation_b = await task_b

    assert conversation_b.id == conversation_a.id
