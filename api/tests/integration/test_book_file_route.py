"""Integration tests for the Range-capable PDF file route:
GET /courses/{course_id}/books/{book_id}/file.

Drives the real ASGI app via `async_client` + `/test-auth/login`, seeding
`Book` rows directly via the ORM (mirrors `test_search_hybrid.py`'s and
`test_book_delete.py`'s helpers) with `storage_path` pointing at a real
PDF-ish file written under `tmp_path`, so `FileResponse` reads real bytes
from disk rather than a mock.

Asserts the plain-GET 200 + Accept-Ranges, the Range-request 206 +
Content-Range (Starlette's native byte-range handling - no manual parser
is exercised or expected here), the cross-course 404 (IDOR guard via
`get_book_in_course`), and the not-ready-book 404.
"""

from pathlib import Path

import pytest
from httpx import AsyncClient
from testcontainers.postgres import PostgresContainer

from app.books.models import Book, BookStatus
from app.courses.models import Course
from app.db.base import async_session_factory
from app.users.models import Role, User
from app.users.repo import upsert_user

PDF_BYTES = b"%PDF-1.7\n" + b"0" * 2048


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
    session,
    course: Course,
    owner: User,
    checksum: str,
    storage_path: Path,
    status: BookStatus = BookStatus.ready,
) -> Book:
    book = Book(
        course_id=course.id,
        uploaded_by=owner.id,
        original_filename="textbook.pdf",
        storage_path=str(storage_path),
        checksum_sha256=checksum,
        size_bytes=len(PDF_BYTES),
        status=status,
    )
    session.add(book)
    await session.flush()
    return book


async def _login(client: AsyncClient, computing_id: str, role: str) -> None:
    response = await client.post(
        "/test-auth/login", json={"computing_id": computing_id, "role": role}
    )
    assert response.status_code == 200


async def _enroll(session, user: User, course: Course) -> None:
    from app.enrollments.models import Enrollment

    session.add(Enrollment(user_id=user.id, course_id=course.id))


@pytest.fixture
def _pdf_file(tmp_path: Path) -> Path:
    path = tmp_path / "book.pdf"
    path.write_bytes(PDF_BYTES)
    return path


async def test_enrolled_user_gets_200_with_accept_ranges(
    async_client: AsyncClient,
    postgres_container: PostgresContainer,
    _pdf_file: Path,
) -> None:
    async with async_session_factory() as session:
        owner = await _make_user(session, "file-prof-1@sfu.ca")
        course = await _make_course(session, owner, "CMPT 430")
        student = await _make_user(session, "file-student-1@sfu.ca", Role.student)
        book = await _make_book(session, course, owner, "a" * 64, _pdf_file)
        await _enroll(session, student, course)
        await session.commit()
        course_id = course.id
        book_id = book.id

    await _login(async_client, "file-student-1", "student")

    response = await async_client.get(f"/courses/{course_id}/books/{book_id}/file")

    assert response.status_code == 200
    assert response.headers["accept-ranges"] == "bytes"
    assert response.headers["content-type"] == "application/pdf"
    assert response.content == PDF_BYTES


async def test_range_request_returns_206_with_content_range(
    async_client: AsyncClient,
    postgres_container: PostgresContainer,
    _pdf_file: Path,
) -> None:
    async with async_session_factory() as session:
        owner = await _make_user(session, "file-prof-2@sfu.ca")
        course = await _make_course(session, owner, "CMPT 431")
        student = await _make_user(session, "file-student-2@sfu.ca", Role.student)
        book = await _make_book(session, course, owner, "b" * 64, _pdf_file)
        await _enroll(session, student, course)
        await session.commit()
        course_id = course.id
        book_id = book.id

    await _login(async_client, "file-student-2", "student")

    response = await async_client.get(
        f"/courses/{course_id}/books/{book_id}/file",
        headers={"Range": "bytes=0-1023"},
    )

    assert response.status_code == 206
    assert "content-range" in response.headers
    assert response.headers["content-range"].startswith("bytes 0-1023/")
    assert len(response.content) == 1024


async def test_cross_course_book_id_404(
    async_client: AsyncClient,
    postgres_container: PostgresContainer,
    _pdf_file: Path,
) -> None:
    async with async_session_factory() as session:
        owner = await _make_user(session, "file-prof-3@sfu.ca")
        course_a = await _make_course(session, owner, "CMPT 432")
        course_b = await _make_course(session, owner, "CMPT 433")
        student = await _make_user(session, "file-student-3@sfu.ca", Role.student)
        book_a = await _make_book(session, course_a, owner, "c" * 64, _pdf_file)
        await _enroll(session, student, course_b)
        await session.commit()
        course_b_id = course_b.id
        book_a_id = book_a.id

    await _login(async_client, "file-student-3", "student")

    # student is enrolled in course_b, but book_a belongs to course_a -
    # requesting it via course_b's path must 404, never leak bytes.
    response = await async_client.get(f"/courses/{course_b_id}/books/{book_a_id}/file")

    assert response.status_code == 404


async def test_not_ready_book_404(
    async_client: AsyncClient,
    postgres_container: PostgresContainer,
    _pdf_file: Path,
) -> None:
    async with async_session_factory() as session:
        owner = await _make_user(session, "file-prof-4@sfu.ca")
        course = await _make_course(session, owner, "CMPT 434")
        student = await _make_user(session, "file-student-4@sfu.ca", Role.student)
        book = await _make_book(
            session, course, owner, "d" * 64, _pdf_file, status=BookStatus.processing
        )
        await _enroll(session, student, course)
        await session.commit()
        course_id = course.id
        book_id = book.id

    await _login(async_client, "file-student-4", "student")

    response = await async_client.get(f"/courses/{course_id}/books/{book_id}/file")

    assert response.status_code == 404
