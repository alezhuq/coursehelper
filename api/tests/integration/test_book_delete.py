"""Integration tests for book delete and retry: the
course owner can delete a book (file removed from disk, row removed) and a
non-owner is denied 403; deleting/retrying a book id from the wrong course
is rejected 404 (cross-course guard); retrying a failed book requeues it
with a fresh deferred job, and retrying a non-failed book is 409.

Drives the real ASGI app via `async_client` + `/test-auth/login` for the
HTTP/authorization surface, and seeds `Book` rows directly via the ORM
(mirrors `test_ingestion_stub.py`'s helpers) so status transitions are
exact. `ingestion_memory_connector` lets retry's `defer_async` run without
a live worker.
"""

from pathlib import Path

import pytest
import pytest_asyncio
from httpx import AsyncClient
from procrastinate.testing import InMemoryConnector
from testcontainers.postgres import PostgresContainer

from app.books.models import Book, BookStatus
from app.courses.models import Course
from app.db.base import async_session_factory
from app.settings import get_settings
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


async def _make_book(
    session, course: Course, owner: User, checksum: str, status: BookStatus = BookStatus.queued
) -> Book:
    book = Book(
        course_id=course.id,
        uploaded_by=owner.id,
        original_filename="textbook.pdf",
        storage_path=f"/data/books/{course.id}/{checksum}.pdf",
        checksum_sha256=checksum,
        size_bytes=1024,
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


@pytest_asyncio.fixture
async def _book_storage_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    settings = get_settings()
    monkeypatch.setattr(settings, "book_storage_dir", str(tmp_path))
    tmp_path.mkdir(parents=True, exist_ok=True)
    return tmp_path


async def test_owner_deletes_book_removes_file_and_row(
    async_client: AsyncClient,
    postgres_container: PostgresContainer,
    ingestion_memory_connector: InMemoryConnector,
    _book_storage_dir: Path,
) -> None:
    async with async_session_factory() as session:
        owner = await _make_user(session, "del-prof-1@sfu.ca")
        course = await _make_course(session, owner, "CMPT 420")
        await session.commit()
        course_id = course.id

    await _login(async_client, "del-prof-1", "professor")

    upload = await async_client.post(
        f"/courses/{course_id}/books",
        files={"file": ("textbook.pdf", b"%PDF-1.7\n" + b"0" * 2048, "application/pdf")},
        headers={"X-Requested-With": "fetch"},
    )
    assert upload.status_code == 201
    book_id = upload.json()["id"]

    async with async_session_factory() as session:
        book = await session.get(Book, book_id)
        assert book is not None
        stored_path = Path(book.storage_path)
        assert stored_path.exists()

    response = await async_client.delete(
        f"/courses/{course_id}/books/{book_id}",
        headers={"X-Requested-With": "fetch"},
    )

    assert response.status_code == 204
    assert not stored_path.exists()

    async with async_session_factory() as session:
        assert await session.get(Book, book_id) is None


async def test_non_owner_delete_403(
    async_client: AsyncClient,
    postgres_container: PostgresContainer,
) -> None:
    async with async_session_factory() as session:
        owner = await _make_user(session, "del-prof-2@sfu.ca")
        course = await _make_course(session, owner, "CMPT 421")
        book = await _make_book(session, course, owner, "d" * 64)
        await session.commit()
        course_id = course.id
        book_id = book.id

    await _login(async_client, "del-prof-2-other", "professor")

    response = await async_client.delete(
        f"/courses/{course_id}/books/{book_id}",
        headers={"X-Requested-With": "fetch"},
    )

    assert response.status_code == 403

    async with async_session_factory() as session:
        assert await session.get(Book, book_id) is not None


async def test_delete_book_from_wrong_course_404(
    async_client: AsyncClient,
    postgres_container: PostgresContainer,
) -> None:
    async with async_session_factory() as session:
        owner = await _make_user(session, "del-prof-3@sfu.ca")
        course_a = await _make_course(session, owner, "CMPT 422")
        course_b = await _make_course(session, owner, "CMPT 423")
        book = await _make_book(session, course_a, owner, "e" * 64)
        await session.commit()
        course_b_id = course_b.id
        book_id = book.id

    await _login(async_client, "del-prof-3", "professor")

    response = await async_client.delete(
        f"/courses/{course_b_id}/books/{book_id}",
        headers={"X-Requested-With": "fetch"},
    )

    assert response.status_code == 404

    async with async_session_factory() as session:
        assert await session.get(Book, book_id) is not None


async def test_retry_failed_book_requeues(
    async_client: AsyncClient,
    postgres_container: PostgresContainer,
    ingestion_memory_connector: InMemoryConnector,
) -> None:
    async with async_session_factory() as session:
        owner = await _make_user(session, "del-prof-4@sfu.ca")
        course = await _make_course(session, owner, "CMPT 424")
        book = await _make_book(session, course, owner, "f" * 64, status=BookStatus.failed)
        book.status_error = "something broke"
        await session.commit()
        course_id = course.id
        book_id = book.id

    await _login(async_client, "del-prof-4", "professor")

    response = await async_client.post(
        f"/courses/{course_id}/books/{book_id}/retry",
        headers={"X-Requested-With": "fetch"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "queued"
    assert body["status_error"] is None

    enqueued = [
        job
        for job in ingestion_memory_connector.jobs.values()
        if job["queueing_lock"] == f"ingest-{book_id}"
    ]
    assert len(enqueued) == 1


async def test_retry_non_failed_book_409(
    async_client: AsyncClient,
    postgres_container: PostgresContainer,
    ingestion_memory_connector: InMemoryConnector,
) -> None:
    async with async_session_factory() as session:
        owner = await _make_user(session, "del-prof-5@sfu.ca")
        course = await _make_course(session, owner, "CMPT 425")
        book = await _make_book(session, course, owner, "g" * 64, status=BookStatus.queued)
        await session.commit()
        course_id = course.id
        book_id = book.id

    await _login(async_client, "del-prof-5", "professor")

    response = await async_client.post(
        f"/courses/{course_id}/books/{book_id}/retry",
        headers={"X-Requested-With": "fetch"},
    )

    assert response.status_code == 409


async def test_failed_row_delete_leaves_file_and_row_intact(
    async_client: AsyncClient,
    postgres_container: PostgresContainer,
    ingestion_memory_connector: InMemoryConnector,
    _book_storage_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Delete is commit-then-unlink: if removing the row fails, the book must
    still be a ready book WITH its PDF, never a row pointing at a file the
    viewer would 500 on."""
    async with async_session_factory() as session:
        owner = await _make_user(session, "del-prof-6@sfu.ca")
        course = await _make_course(session, owner, "CMPT 426")
        await session.commit()
        course_id = course.id

    await _login(async_client, "del-prof-6", "professor")
    upload = await async_client.post(
        f"/courses/{course_id}/books",
        files={"file": ("textbook.pdf", b"%PDF-1.7\n" + b"0" * 2048, "application/pdf")},
        headers={"X-Requested-With": "fetch"},
    )
    assert upload.status_code == 201
    book_id = upload.json()["id"]

    async with async_session_factory() as session:
        book = await session.get(Book, book_id)
        assert book is not None
        stored_path = Path(book.storage_path)
        assert stored_path.exists()

    async def _boom(session, book) -> None:
        raise RuntimeError("database went away")

    monkeypatch.setattr("app.books.repo.delete_book", _boom)

    try:
        response = await async_client.delete(
            f"/courses/{course_id}/books/{book_id}",
            headers={"X-Requested-With": "fetch"},
        )
        assert response.status_code >= 500
    except RuntimeError:
        pass  # the ASGI transport re-raises the unhandled app exception

    assert stored_path.exists()
    async with async_session_factory() as session:
        assert await session.get(Book, book_id) is not None
