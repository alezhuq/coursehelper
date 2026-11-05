"""Integration tests for stalled-ingestion recovery (finding 2).

A book whose ingestion worker died (OOM, restart, deploy) leaves its
procrastinate job in `doing` with a stale (or missing) worker heartbeat and
the book stuck in `processing`. `fail_stalled_ingestions` marks such books
`failed` with a readable `[worker]` message and finishes the job as failed.
It must never re-defer the ingestion (a book that OOM-kills the worker would
otherwise loop forever); the owner presses Retry instead.

Uses `ingestion_memory_connector`: a job row set to `doing` with
`worker_id=None` is what the in-memory connector reports as stalled, and a
job owned by a freshly registered worker has a fresh heartbeat.
"""

from procrastinate.testing import InMemoryConnector
from testcontainers.postgres import PostgresContainer

from app.books.models import Book, BookStatus
from app.courses.models import Course
from app.db.base import async_session_factory
from app.ingestion.procrastinate_app import app as ingestion_app
from app.ingestion.tasks import INTERRUPTED_MESSAGE, fail_stalled_ingestions, ingest_book_stub
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


async def _seed_book(email: str, code: str, checksum: str, status: BookStatus):
    async with async_session_factory() as session:
        owner = await _make_user(session, email)
        course = Course(owner_id=owner.id, code=code, title="Test", term="Fall 2026")
        session.add(course)
        await session.flush()
        book = Book(
            course_id=course.id,
            uploaded_by=owner.id,
            original_filename="textbook.pdf",
            storage_path=f"/data/books/{checksum}.pdf",
            checksum_sha256=checksum,
            size_bytes=1024,
            status=status,
        )
        session.add(book)
        await session.commit()
        return book.id


async def _defer_doing(connector: InMemoryConnector, book_id, worker_id) -> int:
    await ingest_book_stub.configure(queueing_lock=f"ingest-{book_id}").defer_async(
        book_id=str(book_id)
    )
    job_id = next(
        jid for jid, job in connector.jobs.items() if job["queueing_lock"] == f"ingest-{book_id}"
    )
    connector.jobs[job_id]["status"] = "doing"
    connector.jobs[job_id]["worker_id"] = worker_id
    return job_id


async def _book_state(book_id):
    async with async_session_factory() as session:
        book = await session.get(Book, book_id)
        return None if book is None else (book.status, book.status_error)


async def test_stalled_processing_book_is_failed_with_interrupted_message(
    postgres_container: PostgresContainer, ingestion_memory_connector: InMemoryConnector
) -> None:
    book_id = await _seed_book("stall-prof-1@sfu.ca", "CMPT 501", "1" * 64, BookStatus.processing)
    job_id = await _defer_doing(ingestion_memory_connector, book_id, worker_id=None)

    recovered = await fail_stalled_ingestions()

    assert recovered == 1
    assert await _book_state(book_id) == (BookStatus.failed, INTERRUPTED_MESSAGE)
    assert INTERRUPTED_MESSAGE.startswith("[worker]")
    assert ingestion_memory_connector.jobs[job_id]["status"] == "failed"

    # Idempotent: a second tick changes nothing.
    assert await fail_stalled_ingestions() == 0
    assert await _book_state(book_id) == (BookStatus.failed, INTERRUPTED_MESSAGE)


async def test_job_on_a_live_worker_is_left_alone(
    postgres_container: PostgresContainer, ingestion_memory_connector: InMemoryConnector
) -> None:
    book_id = await _seed_book("stall-prof-2@sfu.ca", "CMPT 502", "2" * 64, BookStatus.processing)
    worker_id = await ingestion_app.job_manager.register_worker()
    job_id = await _defer_doing(ingestion_memory_connector, book_id, worker_id=worker_id)

    assert await fail_stalled_ingestions() == 0

    assert await _book_state(book_id) == (BookStatus.processing, None)
    assert ingestion_memory_connector.jobs[job_id]["status"] == "doing"


async def test_stalled_job_for_deleted_or_ready_book_is_finished_without_touching_books(
    postgres_container: PostgresContainer, ingestion_memory_connector: InMemoryConnector
) -> None:
    ready_id = await _seed_book("stall-prof-3@sfu.ca", "CMPT 503", "3" * 64, BookStatus.ready)
    ready_job = await _defer_doing(ingestion_memory_connector, ready_id, worker_id=None)

    deleted_id = await _seed_book(
        "stall-prof-4@sfu.ca", "CMPT 504", "4" * 64, BookStatus.processing
    )
    deleted_job = await _defer_doing(ingestion_memory_connector, deleted_id, worker_id=None)
    async with async_session_factory() as session:
        book = await session.get(Book, deleted_id)
        await session.delete(book)
        await session.commit()

    await fail_stalled_ingestions()  # must not raise

    assert await _book_state(ready_id) == (BookStatus.ready, None)
    assert await _book_state(deleted_id) is None
    assert ingestion_memory_connector.jobs[ready_job]["status"] == "failed"
    assert ingestion_memory_connector.jobs[deleted_job]["status"] == "failed"


async def test_recovery_never_defers_a_new_ingestion_job(
    postgres_container: PostgresContainer, ingestion_memory_connector: InMemoryConnector
) -> None:
    book_id = await _seed_book("stall-prof-5@sfu.ca", "CMPT 505", "5" * 64, BookStatus.processing)
    await _defer_doing(ingestion_memory_connector, book_id, worker_id=None)
    lock = f"ingest-{book_id}"
    before = [j for j in ingestion_memory_connector.jobs.values() if j["queueing_lock"] == lock]

    await fail_stalled_ingestions()

    after = [j for j in ingestion_memory_connector.jobs.values() if j["queueing_lock"] == lock]
    assert len(after) == len(before) == 1
