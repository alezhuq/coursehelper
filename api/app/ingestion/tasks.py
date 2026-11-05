"""Ingestion task: the book status state machine, retry plumbing, and stalled-job
recovery.

`ingest_book_stub` drives a queued `Book` through
`queued -> processing -> ready`, or `-> failed` with a readable
`status_error` recorded on the row. It re-fetches the book at the top of
every run and no-ops silently if it is missing (a book deleted
mid-flight must never crash the worker or leave a phantom job). That
contract extends to a deletion that lands DURING the run: whenever a
failure or commit error surfaces, the book is reloaded on a clean session
and, if it is gone, the task returns silently instead of raising.

`_run_ingestion_stages` is a deliberate seam: it delegates to `run_pipeline`'s real
Docling parse/chunk/embed/write stages - `ingest_book_stub` itself, its
transitions, its error handling, and its `queueing_lock`-guarded retry
contract do not change.

A worker that dies mid-ingestion (OOM kill, restart, deploy) leaves its
procrastinate job in `doing` and the book in `processing` forever.
`fail_stalled_ingestions` (run every minute by the `recover_stalled_ingestions`
periodic task) finds jobs whose worker heartbeat has gone stale, marks the
book `failed` with a readable `[worker]` message, and finishes the job as
failed. It NEVER re-defers the ingestion: a book that OOM-kills the worker
would otherwise loop forever. The owner presses Retry instead.
"""

import logging
import uuid

from procrastinate.jobs import Status
from sqlalchemy.ext.asyncio import AsyncSession

from app.books.models import Book, BookStatus
from app.db.base import async_session_factory
from app.ingestion.pipeline import run_pipeline
from app.ingestion.procrastinate_app import app

logger = logging.getLogger(__name__)

# Well above procrastinate's 10 s heartbeat interval, so a briefly busy but
# live worker is never misjudged as dead.
STALLED_WORKER_SECONDS = 120

INTERRUPTED_MESSAGE = (
    "[worker] Ingestion was interrupted (the worker stopped). Retry to start again."
)


async def _run_ingestion_stages(session: AsyncSession, book: Book) -> None:
    """Seam body: delegates to `run_pipeline` (parse -> chunk ->
    embed -> write+index). The surrounding task (transitions, commit
    sequence, except handler) must not change.
    """
    await run_pipeline(session, book)


async def _reload_book(session: AsyncSession, book_uuid: uuid.UUID) -> Book | None:
    """Recover a session after a failure: roll back whatever aborted
    transaction it holds, drop every identity-map entry (they may be stale or
    point at deleted rows), and re-read the book. Returns None if the book no
    longer exists (deleted mid-flight)."""
    await session.rollback()
    session.expunge_all()
    return await session.get(Book, book_uuid)


@app.task(queue="ingestion")
async def ingest_book_stub(book_id: str) -> None:
    book_uuid = uuid.UUID(book_id)
    async with async_session_factory() as session:
        book = await session.get(Book, book_uuid)
        if book is None:
            # Delete-during-processing race: a missing
            # book is a no-op, never a crashed/failed job.
            return

        book.status = BookStatus.processing
        try:
            await session.commit()
        except Exception:
            # The book vanished between the get and the commit (deleted), or
            # the commit genuinely failed.
            if await _reload_book(session, book_uuid) is None:
                return
            raise

        try:
            await _run_ingestion_stages(session, book)
            book.status = BookStatus.ready
            try:
                await session.commit()
            except Exception as exc:
                raise RuntimeError(f"[write] {exc}") from exc
        except Exception as exc:  # noqa: BLE001 - deliberately broad: any
            # ingestion-stage failure must become a readable status, never
            # an unhandled task crash that leaves the book stuck.
            # The session may hold an aborted transaction (a failed flush or
            # statement), so reload on a clean session before recording the
            # failure; a book deleted mid-flight is a silent no-op.
            failed_book = await _reload_book(session, book_uuid)
            if failed_book is None:
                return
            failed_book.status = BookStatus.failed
            failed_book.status_error = str(exc)[:2000]
            await session.commit()


async def fail_stalled_ingestions() -> int:
    """Fail every ingestion whose worker died; return how many books were
    recovered.

    For each stalled `ingest_book_stub` job the book is marked failed FIRST
    and the job finished second, so a crash between the two steps is simply
    re-detected on the next tick instead of leaving the book stuck. Books
    that are already ready/failed or were deleted are left untouched; their
    job is still finished so it stops being reported.
    """
    stalled = await app.job_manager.get_stalled_jobs(
        task_name=ingest_book_stub.name,
        seconds_since_heartbeat=STALLED_WORKER_SECONDS,
    )
    recovered = 0
    for job in stalled:
        book_uuid = uuid.UUID(str(job.task_kwargs["book_id"]))
        async with async_session_factory() as session:
            book = await session.get(Book, book_uuid)
            if book is not None and book.status in (BookStatus.queued, BookStatus.processing):
                book.status = BookStatus.failed
                book.status_error = INTERRUPTED_MESSAGE
                await session.commit()
                recovered += 1
                logger.warning(
                    "Ingestion of book %s was interrupted (worker stopped); marked failed",
                    book_uuid,
                )
        assert job.id is not None
        await app.job_manager.finish_job_by_id_async(job.id, status=Status.FAILED, delete_job=False)
    return recovered


# Periodic safety net. queueing_lock keeps minute ticks from piling up behind
# a long ingestion; priority above the default lets it run before the next
# queued book. With --concurrency 1 it still waits for any running ingestion
# on the same worker, which is correct: a running job on a live worker is by
# definition not stalled, and a dead worker is restarted by the compose
# restart policy, after which the first tick recovers its orphaned job.
@app.periodic(cron="* * * * *", periodic_id="recover-stalled-ingestions")
@app.task(queue="ingestion", queueing_lock="recover-stalled-ingestions", priority=10)
async def recover_stalled_ingestions(timestamp: int) -> None:
    await fail_stalled_ingestions()
