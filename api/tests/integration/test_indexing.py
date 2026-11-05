"""Integration tests for the chunks table's dual index and the
delete-then-insert idempotency of `replace_chunks_for_book`,
against a real Postgres/pgvector database.

Uses synthetic 1024-dim vectors (not real bge-m3 embeddings) and synthetic
chunk text - this is independent of the ML stack;
only the write+index layer is under test here.
"""

import uuid

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError
from testcontainers.postgres import PostgresContainer

from app.books.models import Book, BookStatus
from app.chunks.models import Chunk
from app.chunks.repo import chunk_id, replace_chunks_for_book
from app.courses.models import Course
from app.db.base import async_session_factory
from app.users.models import Role, User
from app.users.repo import upsert_user


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
        status=BookStatus.queued,
    )
    session.add(book)
    await session.flush()
    return book


def _unit_vector(dim: int, hot_index: int) -> list[float]:
    """A synthetic 1024-dim vector with a 1.0 in one slot, 0.0 elsewhere -
    distinguishable from other synthetic vectors under cosine distance
    without needing a real embedding model."""
    vec = [0.0] * dim
    vec[hot_index] = 1.0
    return vec


async def test_dual_index_and_idempotent_replace(postgres_container: PostgresContainer) -> None:
    async with async_session_factory() as session:
        owner = await _make_user(session, "indexing-prof-1@sfu.ca")
        course = await _make_course(session, owner, "CMPT 410")
        book = await _make_book(session, course, owner, "d" * 64)
        await session.commit()
        book_id = book.id

        chunks = [
            {
                "chunk_index": 0,
                "text": "photosynthesis converts light into chemical energy",
                "page_start": 1,
                "page_end": 2,
                "section_path": ["Chapter 1", "Photosynthesis"],
                "token_count": 8,
            },
            {
                "chunk_index": 1,
                "text": "mitochondria are the powerhouse of the cell",
                "page_start": 2,
                "page_end": 3,
                "section_path": ["Chapter 1", "Cell Biology"],
                "token_count": 8,
            },
            {
                "chunk_index": 2,
                "text": "the krebs cycle produces ATP through oxidation",
                "page_start": 3,
                "page_end": 3,
                "section_path": ["Chapter 1", "Metabolism"],
                "token_count": 8,
            },
        ]
        embeddings = [_unit_vector(1024, i) for i in range(3)]

        await replace_chunks_for_book(session, book_id, chunks, embeddings)
        await session.commit()

        # (1) HNSW cosine query returns the nearest chunk.
        query_vec = _unit_vector(1024, 0)
        nearest = await session.execute(
            select(Chunk)
            .where(Chunk.book_id == book_id)
            .order_by(Chunk.embedding.cosine_distance(query_vec))
            .limit(1)
        )
        nearest_chunk = nearest.scalar_one()
        assert nearest_chunk.chunk_index == 0

        # (2) Full-text tsquery returns the matching chunk, proving
        # content_tsv is generated from the raw text (tsvector
        # index).
        fts = await session.execute(
            select(Chunk).where(
                Chunk.book_id == book_id,
                text("content_tsv @@ plainto_tsquery('english', 'photosynthesis')"),
            )
        )
        fts_chunk = fts.scalar_one()
        assert fts_chunk.chunk_index == 0
        assert "photosynthesis" in fts_chunk.text

        # (3) A second replace with FEWER chunks leaves no duplicates and
        # no stale rows; the surviving row's id
        # equals chunk_id(book_id, 0).
        fewer_chunks = [chunks[0]]
        fewer_embeddings = [embeddings[0]]
        await replace_chunks_for_book(session, book_id, fewer_chunks, fewer_embeddings)
        await session.commit()

        count_result = await session.execute(
            select(func.count()).select_from(Chunk).where(Chunk.book_id == book_id)
        )
        assert count_result.scalar_one() == 1

        surviving = await session.execute(select(Chunk).where(Chunk.book_id == book_id))
        surviving_chunk = surviving.scalar_one()
        assert surviving_chunk.id == chunk_id(book_id, 0)


async def test_duplicate_chunk_index_rejected_by_unique_constraint(
    postgres_container: PostgresContainer,
) -> None:
    async with async_session_factory() as session:
        owner = await _make_user(session, "indexing-prof-2@sfu.ca")
        course = await _make_course(session, owner, "CMPT 411")
        book = await _make_book(session, course, owner, "e" * 64)
        await session.commit()
        book_id = book.id

        session.add_all(
            [
                Chunk(
                    id=uuid.uuid4(),
                    book_id=book_id,
                    chunk_index=0,
                    text="first chunk at index zero",
                    page_start=1,
                    page_end=1,
                    section_path=[],
                    token_count=5,
                    embedding=_unit_vector(1024, 0),
                ),
                Chunk(
                    id=uuid.uuid4(),
                    book_id=book_id,
                    chunk_index=0,
                    text="second chunk colliding at index zero",
                    page_start=1,
                    page_end=1,
                    section_path=[],
                    token_count=5,
                    embedding=_unit_vector(1024, 1),
                ),
            ]
        )
        with pytest.raises(IntegrityError):
            await session.commit()
        await session.rollback()
