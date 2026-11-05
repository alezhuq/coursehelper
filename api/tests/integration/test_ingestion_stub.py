"""Integration tests for the ingestion state machine + retry plumbing:
`ingest_book_stub` drives a queued `Book` through
processing -> ready, or -> failed with a readable `status_error`; a book
deleted mid-flight is a silent no-op; and deferring the task
with a `queueing_lock` enqueues once, with a concurrent second defer caught
as `AlreadyEnqueued` rather than surfaced as an error.

`_run_ingestion_stages` is no longer a no-op (it delegates
to the real `run_pipeline`), so `test_stub_runs_queued_to_ready` (the one
test here that doesn't monkeypatch the seam) points its book at the real
`digital_sample.pdf` fixture rather than a nonexistent stub path - a fake
path would now genuinely fail Docling's parse stage instead of no-opping
through, which isn't what this test is proving.
"""

from pathlib import Path
from uuid import uuid4

import pytest
from procrastinate.exceptions import AlreadyEnqueued
from procrastinate.testing import InMemoryConnector
from sqlalchemy import text
from testcontainers.postgres import PostgresContainer

from app.books.models import Book, BookStatus
from app.chunks.repo import replace_chunks_for_book
from app.courses.models import Course
from app.db.base import async_session_factory
from app.ingestion.tasks import ingest_book_stub
from app.users.models import Role, User
from app.users.repo import upsert_user

FIXTURES_DIR = Path(__file__).resolve().parent.parent / "fixtures"
DIGITAL_SAMPLE = FIXTURES_DIR / "digital_sample.pdf"


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


async def _make_book(
    session, course: Course, owner: User, checksum: str, storage_path: str | None = None
) -> Book:
    book = Book(
        course_id=course.id,
        uploaded_by=owner.id,
        original_filename="textbook.pdf",
        storage_path=storage_path or f"/data/books/{checksum}.pdf",
        checksum_sha256=checksum,
        size_bytes=1024,
        status=BookStatus.queued,
    )
    session.add(book)
    await session.flush()
    return book


async def test_stub_runs_queued_to_ready(postgres_container: PostgresContainer) -> None:
    async with async_session_factory() as session:
        owner = await _make_user(session, "ingest-stub-prof-1@sfu.ca")
        course = await _make_course(session, owner, "CMPT 401")
        book = await _make_book(session, course, owner, "a" * 64, storage_path=str(DIGITAL_SAMPLE))
        await session.commit()
        book_id = book.id

    await ingest_book_stub(book_id=str(book_id))

    async with async_session_factory() as session:
        refreshed = await session.get(Book, book_id)
        assert refreshed is not None
        assert refreshed.status == BookStatus.ready
        assert refreshed.status_error is None


async def test_stub_records_failure(
    postgres_container: PostgresContainer, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with async_session_factory() as session:
        owner = await _make_user(session, "ingest-stub-prof-2@sfu.ca")
        course = await _make_course(session, owner, "CMPT 402")
        book = await _make_book(session, course, owner, "b" * 64)
        await session.commit()
        book_id = book.id

    async def _boom(session, book) -> None:
        raise RuntimeError("boom")

    monkeypatch.setattr("app.ingestion.tasks._run_ingestion_stages", _boom)

    await ingest_book_stub(book_id=str(book_id))

    async with async_session_factory() as session:
        refreshed = await session.get(Book, book_id)
        assert refreshed is not None
        assert refreshed.status == BookStatus.failed
        assert refreshed.status_error is not None
        assert "boom" in refreshed.status_error


async def test_stub_noops_on_deleted_book(postgres_container: PostgresContainer) -> None:
    # No book row exists for this id - the task must return silently
    # instead of raising or creating anything.
    missing_id = uuid4()

    await ingest_book_stub(book_id=str(missing_id))

    async with async_session_factory() as session:
        refreshed = await session.get(Book, missing_id)
        assert refreshed is None


async def test_defer_uses_queueing_lock(
    postgres_container: PostgresContainer, ingestion_memory_connector: InMemoryConnector
) -> None:
    async with async_session_factory() as session:
        owner = await _make_user(session, "ingest-stub-prof-3@sfu.ca")
        course = await _make_course(session, owner, "CMPT 403")
        book = await _make_book(session, course, owner, "c" * 64)
        await session.commit()
        book_id = book.id

    lock = f"ingest-{book_id}"
    await ingest_book_stub.configure(queueing_lock=lock).defer_async(book_id=str(book_id))

    with pytest.raises(AlreadyEnqueued):
        await ingest_book_stub.configure(queueing_lock=lock).defer_async(book_id=str(book_id))

    enqueued_jobs = [
        job for job in ingestion_memory_connector.jobs.values() if job["queueing_lock"] == lock
    ]
    assert len(enqueued_jobs) == 1


async def _seed(email: str, code: str, checksum: str):
    async with async_session_factory() as session:
        owner = await _make_user(session, email)
        course = await _make_course(session, owner, code)
        book = await _make_book(session, course, owner, checksum)
        await session.commit()
        return book.id


async def _delete_book_elsewhere(book_id) -> None:
    async with async_session_factory() as other:
        row = await other.get(Book, book_id)
        assert row is not None
        await other.delete(row)
        await other.commit()


async def test_stub_returns_silently_when_book_deleted_mid_ingestion(
    postgres_container: PostgresContainer, monkeypatch: pytest.MonkeyPatch
) -> None:
    book_id = await _seed("ingest-del-prof-1@sfu.ca", "CMPT 411", "d" * 64)

    async def _stage(session, book) -> None:
        await _delete_book_elsewhere(book.id)

    monkeypatch.setattr("app.ingestion.tasks._run_ingestion_stages", _stage)

    await ingest_book_stub(book_id=str(book_id))  # must not raise

    async with async_session_factory() as session:
        assert await session.get(Book, book_id) is None


async def test_stub_returns_silently_when_write_stage_hits_fk_violation(
    postgres_container: PostgresContainer, monkeypatch: pytest.MonkeyPatch
) -> None:
    book_id = await _seed("ingest-del-prof-2@sfu.ca", "CMPT 412", "e" * 64)

    async def _stage(session, book) -> None:
        await _delete_book_elsewhere(book.id)
        await replace_chunks_for_book(
            session,
            book.id,
            [
                {
                    "chunk_index": 0,
                    "text": "orphan",
                    "page_start": 1,
                    "page_end": 1,
                    "section_path": [],
                    "token_count": 1,
                }
            ],
            [[0.0] * 1024],
        )

    monkeypatch.setattr("app.ingestion.tasks._run_ingestion_stages", _stage)

    await ingest_book_stub(book_id=str(book_id))  # must not raise

    async with async_session_factory() as session:
        assert await session.get(Book, book_id) is None


async def test_stub_fails_readably_when_stage_aborts_the_transaction(
    postgres_container: PostgresContainer, monkeypatch: pytest.MonkeyPatch
) -> None:
    book_id = await _seed("ingest-abort-prof@sfu.ca", "CMPT 413", "f" * 64)

    async def _stage(session, book) -> None:
        await session.execute(text("SELECT 1/0"))

    monkeypatch.setattr("app.ingestion.tasks._run_ingestion_stages", _stage)

    await ingest_book_stub(book_id=str(book_id))  # must not raise

    async with async_session_factory() as session:
        refreshed = await session.get(Book, book_id)
        assert refreshed is not None
        assert refreshed.status == BookStatus.failed
        assert refreshed.status_error is not None
        assert "division by zero" in refreshed.status_error
