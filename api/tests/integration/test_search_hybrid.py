"""Integration tests for the hybrid search endpoint, against a
real Postgres/pgvector database.

`embed_batch` (the real bge-m3 model) is monkeypatched to a deterministic
lookup keyed by query text - this test is about the retrieval/fusion/
authorization/scoping logic in `app/search/{repo,routes}.py`, not the ML
model's embedding quality (that is exercised for real by the
ingestion tests). This mirrors `test_ingestion_failure_messages.py`'s
existing convention of monkeypatching `embed_batch` at its call-site import
name rather than loading the real model in a fast test.

Uses the same synthetic-1024-dim-unit-vector + testcontainers-Postgres
fixture style as `test_indexing.py`.
"""

import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy import select
from testcontainers.postgres import PostgresContainer

from app.books.models import Book, BookStatus
from app.chunks.repo import replace_chunks_for_book
from app.courses.models import Course
from app.db.base import async_session_factory
from app.enrollments.models import Enrollment
from app.users.models import Role, User
from app.users.repo import upsert_user

PHOTOSYNTHESIS_TEXT = "photosynthesis converts light into chemical energy"
MITOCHONDRIA_TEXT = "mitochondria are the powerhouse of the cell"
KREBS_TEXT = "the krebs cycle produces atp through oxidation"
OTHER_COURSE_TEXT = "photosynthesis also occurs in course b materials"

NO_OVERLAP_QUERY = "xylophone jubilant wandering"


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
    """Deterministic stand-in for bge-m3: maps known query strings to the
    unit vector of the chunk they should vector-match exactly (distance 0),
    so vector-arm ranking is fully controlled by the test."""
    mapping = {
        "photosynthesis": _unit_vector(1024, 0),  # matches PHOTOSYNTHESIS_TEXT chunk
        NO_OVERLAP_QUERY: _unit_vector(1024, 1),  # matches MITOCHONDRIA_TEXT chunk
    }
    return [mapping.get(t, _unit_vector(1024, 0)) for t in texts]


@pytest_asyncio.fixture(autouse=True)
def _mock_embed_batch(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.search.routes.embed_batch", _fake_embed_batch)


@pytest_asyncio.fixture
async def seeded(postgres_container: PostgresContainer):
    """Seeds course A (3 chunks, enrolled student) and course B (1 chunk,
    same keyword, student NOT enrolled) - the fixture for every test below."""
    async with async_session_factory() as session:
        owner_a = await _make_user(session, "search-prof-a@sfu.ca")
        course_a = await _make_course(session, owner_a, "BISC 101")
        book_a = await _make_book(session, course_a, owner_a, "a" * 64)
        await session.flush()

        chunks_a = [
            {
                "chunk_index": 0,
                "text": PHOTOSYNTHESIS_TEXT,
                "page_start": 1,
                "page_end": 2,
                "section_path": ["Chapter 1", "Photosynthesis"],
                "token_count": 8,
            },
            {
                "chunk_index": 1,
                "text": MITOCHONDRIA_TEXT,
                "page_start": 2,
                "page_end": 3,
                "section_path": ["Chapter 1", "Cell Biology"],
                "token_count": 8,
            },
            {
                "chunk_index": 2,
                "text": KREBS_TEXT,
                "page_start": 3,
                "page_end": 3,
                "section_path": ["Chapter 1", "Metabolism"],
                "token_count": 8,
            },
        ]
        embeddings_a = [
            _unit_vector(1024, 0),
            _unit_vector(1024, 1),
            _unit_vector(1024, 2),
        ]
        await replace_chunks_for_book(session, book_a.id, chunks_a, embeddings_a)

        owner_b = await _make_user(session, "search-prof-b@sfu.ca")
        course_b = await _make_course(session, owner_b, "BISC 201")
        book_b = await _make_book(session, course_b, owner_b, "b" * 64)
        await session.flush()

        chunks_b = [
            {
                "chunk_index": 0,
                "text": OTHER_COURSE_TEXT,
                "page_start": 1,
                "page_end": 1,
                "section_path": [],
                "token_count": 8,
            }
        ]
        embeddings_b = [_unit_vector(1024, 5)]
        await replace_chunks_for_book(session, book_b.id, chunks_b, embeddings_b)

        await _make_user(session, "search-student@sfu.ca", Role.student)

        await session.commit()

        return {
            "course_a_id": course_a.id,
            "course_b_id": course_b.id,
            "book_a_id": book_a.id,
            "book_b_id": book_b.id,
        }


async def _enroll(course_id, computing_id: str) -> None:
    async with async_session_factory() as session:
        result = await session.execute(select(User).where(User.computing_id == computing_id))
        user = result.scalar_one()
        session.add(Enrollment(user_id=user.id, course_id=course_id))
        await session.commit()


async def test_enrolled_search_returns_fused_results_with_all_display_fields(
    async_client: AsyncClient,
    postgres_container: PostgresContainer,
    seeded: dict,
) -> None:
    await _enroll(seeded["course_a_id"], "search-student")
    await _login(async_client, "search-student", "student")

    response = await async_client.post(
        f"/courses/{seeded['course_a_id']}/search", json={"q": "photosynthesis"}
    )

    assert response.status_code == 200
    results = response.json()
    assert 1 <= len(results) <= 20

    for result in results:
        assert set(result.keys()) == {
            "book_id",
            "original_filename",
            "section_path",
            "page_start",
            "page_end",
            "snippet",
        }

    # Both arms found the photosynthesis chunk (keyword match + exact
    # mocked vector match) - it must fuse to the top result.
    assert results[0]["snippet"]
    assert "\x01" in results[0]["snippet"] and "\x02" in results[0]["snippet"]

    # (f) course scoping: course B's book/chunk must never appear even
    # though its text also contains "photosynthesis".
    returned_book_ids = {result["book_id"] for result in results}
    assert str(seeded["book_b_id"]) not in returned_book_ids


async def test_purely_semantic_query_still_returns_a_plain_snippet(
    async_client: AsyncClient,
    postgres_container: PostgresContainer,
    seeded: dict,
) -> None:
    await _enroll(seeded["course_a_id"], "search-student")
    await _login(async_client, "search-student", "student")

    response = await async_client.post(
        f"/courses/{seeded['course_a_id']}/search", json={"q": NO_OVERLAP_QUERY}
    )

    assert response.status_code == 200
    results = response.json()
    assert len(results) >= 1

    # No keyword arm match for this query against any seeded chunk - the
    # top (vector-only) result's snippet must still be non-empty (native
    # ts_headline fallback), with no highlight markers since nothing
    # matched.
    top = results[0]
    assert top["snippet"]
    assert "\x01" not in top["snippet"]
    assert "\x02" not in top["snippet"]


async def test_blank_query_rejected_422(
    async_client: AsyncClient,
    postgres_container: PostgresContainer,
    seeded: dict,
) -> None:
    await _enroll(seeded["course_a_id"], "search-student")
    await _login(async_client, "search-student", "student")

    empty_response = await async_client.post(
        f"/courses/{seeded['course_a_id']}/search", json={"q": ""}
    )
    whitespace_response = await async_client.post(
        f"/courses/{seeded['course_a_id']}/search", json={"q": "   "}
    )

    assert empty_response.status_code == 422
    assert whitespace_response.status_code == 422


async def test_non_enrolled_user_denied_403(
    async_client: AsyncClient,
    postgres_container: PostgresContainer,
    seeded: dict,
) -> None:
    # search-student is never enrolled in course_a within this test.
    await _login(async_client, "search-student", "student")

    response = await async_client.post(
        f"/courses/{seeded['course_a_id']}/search", json={"q": "photosynthesis"}
    )

    assert response.status_code == 403


async def test_other_course_chunks_never_returned(
    async_client: AsyncClient,
    postgres_container: PostgresContainer,
    seeded: dict,
) -> None:
    await _enroll(seeded["course_a_id"], "search-student")
    await _login(async_client, "search-student", "student")

    response = await async_client.post(
        f"/courses/{seeded['course_a_id']}/search", json={"q": "photosynthesis"}
    )

    assert response.status_code == 200
    returned_book_ids = {result["book_id"] for result in response.json()}
    assert returned_book_ids <= {str(seeded["book_a_id"])}
