"""Multi-turn condensation fast-lane tests: a follow-up question
drives retrieval via its CONDENSED standalone form (never the raw follow-up
text), the first turn of a fresh conversation skips condensation entirely,
and a condensation failure surfaces as a loud SSE `error` event - NEVER a
silent fallback to the raw, un-condensed question (the raw-question spy on
`embed_batch` is the load-bearing assertion: a bad standalone query would
otherwise poison retrieval invisibly to the student).

Reuses the stub-LLM monkeypatch convention (`app.chat.routes.
condense_query` / `app.chat.routes.stream_generation`, patched at their
call-site import names) and the search tests' `_make_user`/`_make_course`/
`_make_book`/`replace_chunks_for_book` seeding helpers - no live Ollama.
"""

import json
import uuid
from typing import Any

import pytest
import pytest_asyncio
from httpx import AsyncClient
from testcontainers.postgres import PostgresContainer

from app.books.models import Book, BookStatus
from app.chat import repo as chat_repo
from app.chat.llm_client import LLMCondenseError
from app.chat.schemas import CitationSpan
from app.chunks.repo import replace_chunks_for_book
from app.courses.models import Course
from app.db.base import async_session_factory
from app.enrollments.models import Enrollment
from app.users.models import Role, User
from app.users.repo import upsert_user

CSRF_HEADERS = {"X-Requested-With": "fetch"}
MITOCHONDRIA_TEXT = "mitochondria are the powerhouse of the cell"
CONDENSED_QUERY_TEXT = "What organelle powers a eukaryotic cell?"


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
    """One course, one book/chunk, one enrolled student - retrieval always
    returns that single chunk regardless of the (stubbed) embedding value."""
    async with async_session_factory() as session:
        owner = await _make_user(session, "condense-prof@sfu.ca")
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

        student = await _make_user(session, "condense-student@sfu.ca", Role.student)
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


async def test_followup_condensation_drives_retrieval_with_standalone_query(
    async_client: AsyncClient,
    postgres_container: PostgresContainer,
    seeded: dict,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A follow-up question in a conversation with history is condensed
    into a standalone retrieval query BEFORE retrieval runs - the
    CONDENSED text, not the raw follow-up, is what drives embed_batch."""
    await _login(async_client, "condense-student", "student")
    conversation_id = await _get_conversation_id(async_client, seeded["course_id"])

    # Seed one prior (user, assistant) turn directly via the repo so the
    # follow-up below has real history to condense against. Two separate
    # transactions (matching the real request flow's two `session.commit()`
    # calls) - `now()` is a transaction-time function, so inserting both
    # rows in one transaction would tie their `created_at` and make
    # `load_recent_turns`' ORDER BY created_at ordering unreliable.
    async with async_session_factory() as session:
        await chat_repo.insert_user_message(session, conversation_id, "What organelle makes ATP?")
        await session.commit()
    async with async_session_factory() as session:
        await chat_repo.insert_assistant_message(
            session,
            conversation_id,
            "The mitochondria. [1]",
            citations=[
                CitationSpan(
                    marker=1,
                    book_id=seeded["book_id"],
                    original_filename="textbook.pdf",
                    page_start=4,
                    page_end=5,
                    section_path=["Chapter 1", "Cell Biology"],
                )
            ],
        )
        await session.commit()

    raw_followup = "How does it do that?"
    condense_calls: list[tuple[list[dict], str]] = []

    async def _fake_condense_query(history: list[dict], question: str) -> str:
        condense_calls.append((history, question))
        return CONDENSED_QUERY_TEXT

    embedded_texts: list[str] = []
    monkeypatch.setattr("app.chat.routes.condense_query", _fake_condense_query)
    monkeypatch.setattr("app.chat.routes.embed_batch", _fake_embed_batch_factory(embedded_texts))
    monkeypatch.setattr("app.chat.routes.stream_generation", _fake_stream_generation)

    response = await _post_message(async_client, seeded["course_id"], conversation_id, raw_followup)
    assert response.status_code == 200

    events = _parse_sse(response.text)
    assert any(event == "done" for event, _ in events)

    assert len(condense_calls) == 1
    history, question = condense_calls[0]
    assert question == raw_followup
    assert history, "the seeded prior turn must be passed to condense_query"
    assert history[-1]["content"] == "The mitochondria. [1]"

    assert embedded_texts == [CONDENSED_QUERY_TEXT]
    assert raw_followup not in embedded_texts


async def test_first_turn_skips_condensation_entirely(
    async_client: AsyncClient,
    postgres_container: PostgresContainer,
    seeded: dict,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The very first turn of a fresh conversation is already standalone -
    condense_query must never be called."""
    await _login(async_client, "condense-student", "student")
    conversation_id = await _get_conversation_id(async_client, seeded["course_id"])

    def _condense_query_must_not_be_called(*_args, **_kwargs):
        raise AssertionError("condense_query must not be called on the first turn")

    embedded_texts: list[str] = []
    monkeypatch.setattr("app.chat.routes.condense_query", _condense_query_must_not_be_called)
    monkeypatch.setattr("app.chat.routes.embed_batch", _fake_embed_batch_factory(embedded_texts))
    monkeypatch.setattr("app.chat.routes.stream_generation", _fake_stream_generation)

    first_question = "What powers the cell?"
    response = await _post_message(
        async_client, seeded["course_id"], conversation_id, first_question
    )
    assert response.status_code == 200

    events = _parse_sse(response.text)
    assert any(event == "done" for event, _ in events)
    assert embedded_texts == [first_question]


async def test_condensation_failure_never_falls_back_to_raw_question(
    async_client: AsyncClient,
    postgres_container: PostgresContainer,
    seeded: dict,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A condensation failure (3 consecutive invalid-JSON attempts,
    LLMCondenseError) surfaces as a loud SSE `error` event with a
    plain-language message - retrieval must NEVER run with the raw,
    un-condensed follow-up question (the never-silent-fallback contract)."""
    await _login(async_client, "condense-student", "student")
    conversation_id = await _get_conversation_id(async_client, seeded["course_id"])

    async with async_session_factory() as session:
        await chat_repo.insert_user_message(session, conversation_id, "What organelle makes ATP?")
        await session.commit()
    async with async_session_factory() as session:
        await chat_repo.insert_assistant_message(
            session, conversation_id, "The mitochondria. [1]", citations=[]
        )
        await session.commit()

    async def _failing_condense_query(_history: list[dict], _question: str) -> str:
        raise LLMCondenseError("Could not produce a valid standalone query.")

    embed_calls: list[str] = []
    monkeypatch.setattr("app.chat.routes.condense_query", _failing_condense_query)
    monkeypatch.setattr("app.chat.routes.embed_batch", _fake_embed_batch_factory(embed_calls))

    def _stream_generation_must_not_be_called(*_args, **_kwargs):
        raise AssertionError("stream_generation must not be called after a condensation failure")

    monkeypatch.setattr("app.chat.routes.stream_generation", _stream_generation_must_not_be_called)

    raw_followup = "How does it do that?"
    response = await _post_message(async_client, seeded["course_id"], conversation_id, raw_followup)
    assert response.status_code == 200

    events = _parse_sse(response.text)
    error_events = [data for event, data in events if event == "error"]
    assert error_events == ["Something went wrong preparing your question. Please try again."]
    assert not any(event == "done" for event, _ in events)
    assert not any(event == "citations" for event, _ in events)

    # The load-bearing assertion: the raw follow-up NEVER reached retrieval.
    assert embed_calls == []
