"""Integration test: retrying an ingestion never duplicates chunks (edge idempotency).

Runs the real pipeline once on `digital_sample.pdf` to `ready`, captures the
chunk count and ids, then runs `ingest_book_stub` again on the same book
(simulating a retry - e.g. a user-triggered re-ingestion after a transient
failure) and asserts the second run produces the identical chunk count and
identical chunk ids, proving `replace_chunks_for_book`'s delete-then-insert
plus the deterministic `chunk_id(book_id, chunk_index)` derivation together
guarantee no duplication end-to-end through the real pipeline (not just at
the repo-layer unit level).
"""

from pathlib import Path

from sqlalchemy import select
from testcontainers.postgres import PostgresContainer

from app.books.models import Book, BookStatus
from app.chunks.models import Chunk
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


async def _make_book(session, course: Course, owner: User, checksum: str) -> Book:
    book = Book(
        course_id=course.id,
        uploaded_by=owner.id,
        original_filename="digital_sample.pdf",
        storage_path=str(DIGITAL_SAMPLE),
        checksum_sha256=checksum,
        size_bytes=1024,
        status=BookStatus.queued,
    )
    session.add(book)
    await session.flush()
    return book


async def test_retrying_ingestion_does_not_duplicate_chunks(
    postgres_container: PostgresContainer,
) -> None:
    async with async_session_factory() as session:
        owner = await _make_user(session, "retry-prof-1@sfu.ca")
        course = await _make_course(session, owner, "CMPT 440")
        book = await _make_book(session, course, owner, "9" * 64)
        await session.commit()
        book_id = book.id

    # First run: reaches ready with a real chunk set.
    await ingest_book_stub(book_id=str(book_id))

    async with async_session_factory() as session:
        refreshed = await session.get(Book, book_id)
        assert refreshed is not None
        assert refreshed.status == BookStatus.ready

        first_run = await session.execute(
            select(Chunk).where(Chunk.book_id == book_id).order_by(Chunk.chunk_index)
        )
        first_chunks = first_run.scalars().all()
        first_count = len(first_chunks)
        first_ids = [c.id for c in first_chunks]
        assert first_count > 0

    # Reset to queued (simulating a user-triggered retry) and run again.
    async with async_session_factory() as session:
        refreshed = await session.get(Book, book_id)
        assert refreshed is not None
        refreshed.status = BookStatus.queued
        refreshed.status_error = None
        await session.commit()

    await ingest_book_stub(book_id=str(book_id))

    async with async_session_factory() as session:
        refreshed = await session.get(Book, book_id)
        assert refreshed is not None
        assert refreshed.status == BookStatus.ready
        assert refreshed.status_error is None

        second_run = await session.execute(
            select(Chunk).where(Chunk.book_id == book_id).order_by(Chunk.chunk_index)
        )
        second_chunks = second_run.scalars().all()
        second_count = len(second_chunks)
        second_ids = [c.id for c in second_chunks]

    assert second_count == first_count
    assert second_ids == first_ids
