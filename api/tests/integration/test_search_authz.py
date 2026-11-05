"""Cross-cutting enrollment deny matrix: a single
suite proving non-enrolled users are blocked from both
search and PDF bytes, cross-course book access always 404s (never bytes),
and enrollment revocation takes effect on the very next request.

Seeds course A (owner_a, an enrolled student, a ready book with one chunk)
and course B (owner_b, a ready book, no shared enrollment) - mirrors the
`_make_user`/`_make_course`/`_make_book` helpers already used by
`test_search_hybrid.py` and `test_book_file_route.py`. `embed_batch` is
monkeypatched exactly as in `test_search_hybrid.py` so the search arm never
loads the real bge-m3 model.
"""

from pathlib import Path

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

PDF_BYTES = b"%PDF-1.7\n" + b"0" * 2048


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


async def _make_book(
    session, course: Course, owner: User, checksum: str, storage_path: Path
) -> Book:
    book = Book(
        course_id=course.id,
        uploaded_by=owner.id,
        original_filename="textbook.pdf",
        storage_path=str(storage_path),
        checksum_sha256=checksum,
        size_bytes=len(PDF_BYTES),
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
        "app.search.routes.embed_batch",
        lambda texts: [_unit_vector(1024, 0) for _ in texts],
    )


@pytest.fixture
def _pdf_file(tmp_path: Path) -> Path:
    path = tmp_path / "book.pdf"
    path.write_bytes(PDF_BYTES)
    return path


@pytest_asyncio.fixture
async def seeded(postgres_container: PostgresContainer, _pdf_file: Path):
    """Course A: owner_a, an enrolled student, a ready book with one chunk.
    Course B: owner_b, a ready book, no shared enrollment - a second
    student exists but is never enrolled in course A."""
    async with async_session_factory() as session:
        owner_a = await _make_user(session, "authz-prof-a@sfu.ca")
        course_a = await _make_course(session, owner_a, "CMPT 440")
        book_a = await _make_book(session, course_a, owner_a, "aa" * 32, _pdf_file)
        await session.flush()

        await replace_chunks_for_book(
            session,
            book_a.id,
            [
                {
                    "chunk_index": 0,
                    "text": "mitochondria are the powerhouse of the cell",
                    "page_start": 1,
                    "page_end": 1,
                    "section_path": [],
                    "token_count": 8,
                }
            ],
            [_unit_vector(1024, 0)],
        )

        owner_b = await _make_user(session, "authz-prof-b@sfu.ca")
        course_b = await _make_course(session, owner_b, "CMPT 441")
        book_b = await _make_book(session, course_b, owner_b, "bb" * 32, _pdf_file)
        await session.flush()

        enrolled_student = await _make_user(session, "authz-student-enrolled@sfu.ca", Role.student)
        session.add(Enrollment(user_id=enrolled_student.id, course_id=course_a.id))

        # denied_student is never enrolled anywhere.
        await _make_user(session, "authz-student-denied@sfu.ca", Role.student)

        await session.commit()

        return {
            "course_a_id": course_a.id,
            "course_b_id": course_b.id,
            "book_a_id": book_a.id,
            "book_b_id": book_b.id,
        }


async def test_non_enrolled_student_denied_search(
    async_client: AsyncClient,
    postgres_container: PostgresContainer,
    seeded: dict,
) -> None:
    await _login(async_client, "authz-student-denied", "student")

    response = await async_client.post(
        f"/courses/{seeded['course_a_id']}/search", json={"q": "mitochondria"}
    )

    assert response.status_code == 403


async def test_non_enrolled_student_denied_file(
    async_client: AsyncClient,
    postgres_container: PostgresContainer,
    seeded: dict,
) -> None:
    await _login(async_client, "authz-student-denied", "student")

    response = await async_client.get(
        f"/courses/{seeded['course_a_id']}/books/{seeded['book_a_id']}/file"
    )

    assert response.status_code == 403


async def test_cross_course_book_via_enrolled_course_404s_not_bytes(
    async_client: AsyncClient,
    postgres_container: PostgresContainer,
    seeded: dict,
) -> None:
    await _login(async_client, "authz-student-enrolled", "student")

    # Enrolled in course A, but book_b belongs to course B - requesting it
    # through course A's path must 404 (IDOR guard), never return bytes.
    response = await async_client.get(
        f"/courses/{seeded['course_a_id']}/books/{seeded['book_b_id']}/file"
    )

    assert response.status_code == 404


async def test_mid_session_enrollment_revocation_flips_next_request_to_403(
    async_client: AsyncClient,
    postgres_container: PostgresContainer,
    seeded: dict,
) -> None:
    await _login(async_client, "authz-student-enrolled", "student")

    first = await async_client.get(
        f"/courses/{seeded['course_a_id']}/books/{seeded['book_a_id']}/file"
    )
    assert first.status_code == 200

    async with async_session_factory() as session:
        result = await session.execute(
            Enrollment.__table__.delete().where(Enrollment.course_id == seeded["course_a_id"])
        )
        await session.commit()
        assert result.rowcount >= 1

    second = await async_client.get(
        f"/courses/{seeded['course_a_id']}/books/{seeded['book_a_id']}/file"
    )

    assert second.status_code == 403
