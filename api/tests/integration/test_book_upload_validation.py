"""Integration tests for PDF upload validation: a course owner can
upload a valid PDF and get a queued `Book` + one deferred ingestion job; a
non-PDF, an oversize file, and a per-course duplicate checksum are each
rejected with the exact user-facing message; two concurrent
uploads of the identical file resolve to exactly one 201 and one 409; and a
non-owner is rejected 403.

Drives the real ASGI app via `async_client` + `/test-auth/login`, and uses
`ingestion_memory_connector` so the upload route's `defer_async` runs
without a live worker. `book_storage_dir` is monkeypatched to a pytest
`tmp_path` per test so uploaded bytes never touch a real deployment path.
"""

import asyncio
from pathlib import Path

import pytest
import pytest_asyncio
from httpx import AsyncClient
from procrastinate.testing import InMemoryConnector
from sqlalchemy import select
from testcontainers.postgres import PostgresContainer

from app.books.models import Book, BookStatus
from app.courses.models import Course
from app.db.base import async_session_factory
from app.settings import get_settings
from app.users.models import Role, User
from app.users.repo import upsert_user

VALID_PDF_BYTES = b"%PDF-1.7\n" + b"0" * 2048
NON_PDF_BYTES = b"this is definitely not a pdf file" * 100
NON_PDF_MESSAGE = "This file is not a PDF. Only PDF files can be uploaded."
OVERSIZE_MESSAGE = "This file is larger than the 300 MB limit. Compress or split it and try again."
DUPLICATE_MESSAGE = "This PDF has already been uploaded to this course."


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


async def _login(client: AsyncClient, computing_id: str, role: str) -> None:
    response = await client.post(
        "/test-auth/login", json={"computing_id": computing_id, "role": role}
    )
    assert response.status_code == 200


@pytest_asyncio.fixture
async def _book_storage_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point `book_storage_dir` at a pytest tmp_path for the duration of a
    test, and clear it back afterwards - the settings object is a
    process-wide `lru_cache`'d singleton, so the monkeypatch on the
    attribute is what makes it test-local.
    """
    settings = get_settings()
    monkeypatch.setattr(settings, "book_storage_dir", str(tmp_path))
    tmp_path.mkdir(parents=True, exist_ok=True)
    return tmp_path


async def test_valid_pdf_upload_creates_queued_book(
    async_client: AsyncClient,
    postgres_container: PostgresContainer,
    ingestion_memory_connector: InMemoryConnector,
    _book_storage_dir: Path,
) -> None:
    async with async_session_factory() as session:
        owner = await _make_user(session, "upload-prof-1@sfu.ca")
        course = await _make_course(session, owner, "CMPT 410")
        await session.commit()
        course_id = course.id

    await _login(async_client, "upload-prof-1", "professor")

    response = await async_client.post(
        f"/courses/{course_id}/books",
        files={"file": ("textbook.pdf", VALID_PDF_BYTES, "application/pdf")},
        headers={"X-Requested-With": "fetch"},
    )

    assert response.status_code == 201
    body = response.json()
    assert body["status"] == "queued"

    async with async_session_factory() as session:
        result = await session.execute(select(Book).where(Book.course_id == course_id))
        books = result.scalars().all()
        assert len(books) == 1
        assert str(books[0].id) == body["id"]

    enqueued = [
        job
        for job in ingestion_memory_connector.jobs.values()
        if job["queueing_lock"] == f"ingest-{body['id']}"
    ]
    assert len(enqueued) == 1


async def test_non_pdf_rejected_422(
    async_client: AsyncClient,
    postgres_container: PostgresContainer,
    ingestion_memory_connector: InMemoryConnector,
    _book_storage_dir: Path,
) -> None:
    async with async_session_factory() as session:
        owner = await _make_user(session, "upload-prof-2@sfu.ca")
        course = await _make_course(session, owner, "CMPT 411")
        await session.commit()
        course_id = course.id

    await _login(async_client, "upload-prof-2", "professor")

    response = await async_client.post(
        f"/courses/{course_id}/books",
        files={"file": ("notreally.pdf", NON_PDF_BYTES, "application/pdf")},
        headers={"X-Requested-With": "fetch"},
    )

    assert response.status_code == 422
    assert response.json()["detail"] == NON_PDF_MESSAGE


async def test_oversize_rejected_413(
    async_client: AsyncClient,
    postgres_container: PostgresContainer,
    ingestion_memory_connector: InMemoryConnector,
    _book_storage_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "max_upload_bytes", 1024)

    async with async_session_factory() as session:
        owner = await _make_user(session, "upload-prof-3@sfu.ca")
        course = await _make_course(session, owner, "CMPT 412")
        await session.commit()
        course_id = course.id

    await _login(async_client, "upload-prof-3", "professor")

    response = await async_client.post(
        f"/courses/{course_id}/books",
        files={"file": ("big.pdf", VALID_PDF_BYTES, "application/pdf")},
        headers={"X-Requested-With": "fetch"},
    )

    assert response.status_code == 413
    assert response.json()["detail"] == OVERSIZE_MESSAGE

    async with async_session_factory() as session:
        result = await session.execute(select(Book).where(Book.course_id == course_id))
        assert result.scalars().all() == []

    tmp_dir = _book_storage_dir / "tmp"
    leaked = list(tmp_dir.glob("*")) if tmp_dir.exists() else []
    assert leaked == []


async def test_duplicate_checksum_rejected_409(
    async_client: AsyncClient,
    postgres_container: PostgresContainer,
    ingestion_memory_connector: InMemoryConnector,
    _book_storage_dir: Path,
) -> None:
    async with async_session_factory() as session:
        owner = await _make_user(session, "upload-prof-4@sfu.ca")
        course = await _make_course(session, owner, "CMPT 413")
        await session.commit()
        course_id = course.id

    await _login(async_client, "upload-prof-4", "professor")

    first = await async_client.post(
        f"/courses/{course_id}/books",
        files={"file": ("textbook.pdf", VALID_PDF_BYTES, "application/pdf")},
        headers={"X-Requested-With": "fetch"},
    )
    second = await async_client.post(
        f"/courses/{course_id}/books",
        files={"file": ("textbook-copy.pdf", VALID_PDF_BYTES, "application/pdf")},
        headers={"X-Requested-With": "fetch"},
    )

    assert first.status_code == 201
    assert second.status_code == 409
    assert second.json()["detail"] == DUPLICATE_MESSAGE

    async with async_session_factory() as session:
        result = await session.execute(select(Book).where(Book.course_id == course_id))
        assert len(result.scalars().all()) == 1


async def test_concurrent_duplicate_upload(
    async_client: AsyncClient,
    postgres_container: PostgresContainer,
    ingestion_memory_connector: InMemoryConnector,
    _book_storage_dir: Path,
) -> None:
    async with async_session_factory() as session:
        owner = await _make_user(session, "upload-prof-5@sfu.ca")
        course = await _make_course(session, owner, "CMPT 414")
        await session.commit()
        course_id = course.id

    await _login(async_client, "upload-prof-5", "professor")

    async def _upload(filename: str):
        return await async_client.post(
            f"/courses/{course_id}/books",
            files={"file": (filename, VALID_PDF_BYTES, "application/pdf")},
            headers={"X-Requested-With": "fetch"},
        )

    first, second = await asyncio.gather(_upload("a.pdf"), _upload("b.pdf"))

    statuses = sorted([first.status_code, second.status_code])
    assert statuses == [201, 409]

    async with async_session_factory() as session:
        result = await session.execute(select(Book).where(Book.course_id == course_id))
        assert len(result.scalars().all()) == 1


async def test_non_owner_cannot_upload_403(
    async_client: AsyncClient,
    postgres_container: PostgresContainer,
    ingestion_memory_connector: InMemoryConnector,
    _book_storage_dir: Path,
) -> None:
    async with async_session_factory() as session:
        owner = await _make_user(session, "upload-prof-6@sfu.ca")
        course = await _make_course(session, owner, "CMPT 415")
        await session.commit()
        course_id = course.id

    await _login(async_client, "upload-prof-6-other", "professor")

    response = await async_client.post(
        f"/courses/{course_id}/books",
        files={"file": ("textbook.pdf", VALID_PDF_BYTES, "application/pdf")},
        headers={"X-Requested-With": "fetch"},
    )

    assert response.status_code == 403


async def test_promote_failure_removes_orphaned_book(
    async_client: AsyncClient,
    postgres_container: PostgresContainer,
    ingestion_memory_connector: InMemoryConnector,
    _book_storage_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If the file cannot be moved into place after `create_book` has already
    committed the row, the orphaned `queued` book (which retry can never
    recover - retry only accepts `failed`) is removed, so a failed upload
    leaves no stranded row and no leaked file.
    """

    def _boom_promote(temp_path: Path, final_path: Path) -> None:
        raise OSError("simulated storage failure")

    monkeypatch.setattr("app.books.routes.promote_temp_to_final", _boom_promote)

    async with async_session_factory() as session:
        owner = await _make_user(session, "upload-promote-fail@sfu.ca")
        course = await _make_course(session, owner, "CMPT 420")
        await session.commit()
        course_id = course.id

    await _login(async_client, "upload-promote-fail", "professor")

    response = await async_client.post(
        f"/courses/{course_id}/books",
        files={"file": ("textbook.pdf", VALID_PDF_BYTES, "application/pdf")},
        headers={"X-Requested-With": "fetch"},
    )

    assert response.status_code == 500

    async with async_session_factory() as session:
        result = await session.execute(select(Book).where(Book.course_id == course_id))
        assert result.scalars().all() == []  # no stranded 'queued' row

    tmp_dir = _book_storage_dir / "tmp"
    leaked = list(tmp_dir.glob("*")) if tmp_dir.exists() else []
    assert leaked == []


async def test_defer_failure_marks_book_failed_not_queued(
    async_client: AsyncClient,
    postgres_container: PostgresContainer,
    ingestion_memory_connector: InMemoryConnector,
    _book_storage_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If enqueuing the ingestion job fails for any reason other than a benign
    `AlreadyEnqueued`, the committed book is marked `failed` with a readable
    error - never left stranded in `queued` with no job - so the owner can
    Retry it (retry only accepts `failed`).
    """

    class _BoomTask:
        def configure(self, **_kwargs: object) -> "_BoomTask":
            return self

        async def defer_async(self, **_kwargs: object) -> None:
            raise RuntimeError("procrastinate unavailable")

    monkeypatch.setattr("app.books.routes.ingest_book_stub", _BoomTask())

    async with async_session_factory() as session:
        owner = await _make_user(session, "upload-defer-fail@sfu.ca")
        course = await _make_course(session, owner, "CMPT 421")
        await session.commit()
        course_id = course.id

    await _login(async_client, "upload-defer-fail", "professor")

    response = await async_client.post(
        f"/courses/{course_id}/books",
        files={"file": ("textbook.pdf", VALID_PDF_BYTES, "application/pdf")},
        headers={"X-Requested-With": "fetch"},
    )

    assert response.status_code == 201
    body = response.json()
    assert body["status"] == "failed"
    assert body["status_error"]

    async with async_session_factory() as session:
        result = await session.execute(select(Book).where(Book.course_id == course_id))
        books = result.scalars().all()
        assert len(books) == 1
        assert books[0].status == BookStatus.failed
        assert books[0].status_error
