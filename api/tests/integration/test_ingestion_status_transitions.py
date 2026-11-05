"""Integration test: the full pipeline end-to-end on a real PDF
plus the processing-then-ready status-transition contract.

Drives `ingest_book_stub` against `digital_sample.pdf` through the real
Docling/HybridChunker/bge-m3 stages (no mocking of parse/chunk/embed) and
asserts the book resolves to `ready` with real persisted chunks retrievable
via both the HNSW vector index and the tsvector full-text index - the first
honest end-to-end slice of the ingestion pipeline.
"""

from pathlib import Path

import pytest
from sqlalchemy import select, text
from testcontainers.postgres import PostgresContainer

from app.books.models import Book, BookStatus
from app.chunks.models import Chunk
from app.courses.models import Course
from app.db.base import async_session_factory
from app.ingestion.embedding import embed_batch
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


async def test_pipeline_reaches_ready_with_dual_indexed_chunks(
    postgres_container: PostgresContainer,
) -> None:
    async with async_session_factory() as session:
        owner = await _make_user(session, "pipeline-prof-1@sfu.ca")
        course = await _make_course(session, owner, "CMPT 420")
        book = await _make_book(session, course, owner, "f" * 64)
        await session.commit()
        book_id = book.id

    await ingest_book_stub(book_id=str(book_id))

    async with async_session_factory() as session:
        refreshed = await session.get(Book, book_id)
        assert refreshed is not None
        assert refreshed.status == BookStatus.ready
        assert refreshed.status_error is None

        chunks_result = await session.execute(select(Chunk).where(Chunk.book_id == book_id))
        chunks = chunks_result.scalars().all()
        assert len(chunks) > 0

        # (1) HNSW cosine query returns a chunk for this book.
        query_vec = embed_batch(["photosynthesis converts light into chemical energy"])[0]
        nearest = await session.execute(
            select(Chunk)
            .where(Chunk.book_id == book_id)
            .order_by(Chunk.embedding.cosine_distance(query_vec))
            .limit(1)
        )
        assert nearest.scalar_one() is not None

        # (2) Full-text tsquery returns a chunk for this book.
        fts = await session.execute(
            select(Chunk).where(
                Chunk.book_id == book_id,
                text("content_tsv @@ plainto_tsquery('english', 'photosynthesis')"),
            )
        )
        assert fts.scalars().first() is not None


async def test_book_is_processing_mid_run_before_stages_complete(
    postgres_container: PostgresContainer, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Extends the pattern in test_ingestion_stub.py: the book must
    be observably `processing` (committed) before `_run_ingestion_stages`
    runs, proving the commit-before-stages ordering survived the
    seam wiring unchanged."""
    async with async_session_factory() as session:
        owner = await _make_user(session, "pipeline-prof-2@sfu.ca")
        course = await _make_course(session, owner, "CMPT 421")
        book = await _make_book(session, course, owner, "f" * 63 + "1")
        await session.commit()
        book_id = book.id

    observed_status: list[BookStatus] = []

    async def _observe_and_noop(session, book) -> None:
        async with async_session_factory() as check_session:
            mid_run = await check_session.get(Book, book_id)
            assert mid_run is not None
            observed_status.append(mid_run.status)

    monkeypatch.setattr("app.ingestion.tasks._run_ingestion_stages", _observe_and_noop)

    await ingest_book_stub(book_id=str(book_id))

    assert observed_status == [BookStatus.processing]
