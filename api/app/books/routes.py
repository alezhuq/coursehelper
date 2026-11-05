"""Book routes: upload, list, delete, retry.

Nested under `/courses/{course_id}/books` so `require_course_owner_or_admin`
(upload/delete/retry) and `require_course_enrollment` (list) resolve access
strictly from `(user, course_id)` - the structural constraint documented in
`app/authz/dependencies.py`. Every mutating route also requires the custom
CSRF header (`app/auth/csrf.py`), matching the app's same-origin CSRF
model.

Upload ordering: stream to a temp file and
compute its checksum -> attempt the atomic INSERT-or-409 (`repo.
create_book`) -> only on success, `os.replace` the temp file into its
canonical path -> only then defer the ingestion job. The temp file is
always removed on any failure so a rejected upload never leaks disk space.
"""

import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, UploadFile
from fastapi.responses import FileResponse
from procrastinate.exceptions import AlreadyEnqueued
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.csrf import require_custom_header
from app.authz.dependencies import require_course_enrollment, require_course_owner_or_admin
from app.books import repo
from app.books.models import Book, BookStatus
from app.books.schemas import BookResponse
from app.books.storage import promote_temp_to_final, remove_quietly, stream_upload_to_temp
from app.db.base import get_session
from app.ingestion.procrastinate_app import app as ingestion_app
from app.ingestion.tasks import ingest_book_stub
from app.settings import get_settings
from app.users.models import User

router = APIRouter(prefix="/courses/{course_id}/books", tags=["books"])

RETRY_NOT_FAILED_MESSAGE = "Only a failed book can be retried"
BOOK_NOT_READY_MESSAGE = "This book is not available yet."


async def _defer_or_fail(session: AsyncSession, book: Book) -> None:
    """Enqueue the ingestion job for an already-committed book.

    `AlreadyEnqueued` is benign - a job is already waiting (double-click or a
    concurrent retry). Any OTHER enqueue failure marks the book `failed` with a
    readable `status_error` and commits, so it is never stranded in `queued`
    with no job: the owner can then Retry it (retry only accepts `failed`).
    """
    try:
        await ingest_book_stub.configure(queueing_lock=f"ingest-{book.id}").defer_async(
            book_id=str(book.id)
        )
    except AlreadyEnqueued:
        pass  # a job is already waiting - double-click/concurrent retry, not an error
    except Exception as exc:  # noqa: BLE001 - any enqueue failure must stay recoverable
        book.status = BookStatus.failed
        book.status_error = f"Could not queue ingestion: {exc}"[:2000]
        await session.commit()


async def _cancel_ingestion_job(book_id: uuid.UUID) -> None:
    """Best-effort cancel of any still-queued ingestion job for this book.

    Correctness never rests on this succeeding - `ingest_book_stub`
    silently no-ops on a missing book row, which is
    the actual guarantee. This is only a courtesy to stop wasted work
    sooner, so any failure here is swallowed rather than blocking delete.
    """
    try:
        lock = f"ingest-{book_id}"
        jobs = await ingestion_app.job_manager.list_jobs_async(queueing_lock=lock)
        for job in jobs:
            if job.id is not None:
                await ingestion_app.job_manager.cancel_job_by_id_async(job.id)
    except Exception:  # noqa: BLE001 - deliberately broad: best-effort only.
        pass


@router.post(
    "",
    response_model=BookResponse,
    status_code=201,
    dependencies=[Depends(require_custom_header)],
)
async def upload_book(
    course_id: uuid.UUID,
    file: UploadFile,
    user: User = Depends(require_course_owner_or_admin),
    session: AsyncSession = Depends(get_session),
) -> Book:
    settings = get_settings()
    book_id = uuid.uuid4()
    final_path = Path(settings.book_storage_dir) / str(course_id) / f"{book_id}.pdf"
    tmp_dir = Path(settings.book_storage_dir) / "tmp"

    temp_path, checksum, size = await stream_upload_to_temp(
        file, tmp_dir, settings.max_upload_bytes
    )
    try:
        # create_book commits the row as 'queued' (or raises 409 + rolls back,
        # leaving no row - that error propagates directly).
        book = await repo.create_book(
            session,
            id=book_id,
            course_id=course_id,
            uploaded_by=user.id,
            filename=file.filename or "upload.pdf",
            storage_path=str(final_path),
            checksum=checksum,
            size=size,
        )
        try:
            promote_temp_to_final(temp_path, final_path)
        except Exception as exc:
            # The row is committed 'queued' but the file could not be stored -
            # it would be stranded (retry only accepts 'failed', and there is no
            # file to re-ingest anyway). Remove the orphaned row so a failed
            # upload leaves nothing behind, mirroring the temp-file guarantee,
            # then surface a clean 500 (the client maps 5xx to a retry message).
            await repo.delete_book(session, book)
            await session.commit()
            raise HTTPException(500, "The file could not be stored. Please try again.") from exc
    finally:
        # No-op if promote_temp_to_final already moved the file; removes a
        # leftover temp file on any failure path (e.g. a 409 duplicate).
        remove_quietly(temp_path)

    await _defer_or_fail(session, book)
    return book


@router.get("", response_model=list[BookResponse])
async def list_books(
    course_id: uuid.UUID,
    _user: User = Depends(require_course_enrollment),
    session: AsyncSession = Depends(get_session),
) -> list[Book]:
    return await repo.list_books_for_course(session, course_id)


@router.delete(
    "/{book_id}",
    status_code=204,
    dependencies=[Depends(require_custom_header)],
)
async def delete_book(
    course_id: uuid.UUID,
    book_id: uuid.UUID,
    _user: User = Depends(require_course_owner_or_admin),
    session: AsyncSession = Depends(get_session),
) -> None:
    book = await repo.get_book_in_course(session, course_id, book_id)
    await _cancel_ingestion_job(book.id)
    storage_path = book.storage_path
    await repo.delete_book(session, book)
    await session.commit()
    # Unlink only AFTER the row delete committed: a failed delete/commit must
    # leave a ready book with its PDF (otherwise the viewer would 500 on a
    # row whose file is gone), while an orphan file after a successful commit
    # costs only disk space.
    remove_quietly(storage_path)


@router.post(
    "/{book_id}/retry",
    response_model=BookResponse,
    dependencies=[Depends(require_custom_header)],
)
async def retry_book(
    course_id: uuid.UUID,
    book_id: uuid.UUID,
    _user: User = Depends(require_course_owner_or_admin),
    session: AsyncSession = Depends(get_session),
) -> Book:
    book = await repo.get_book_in_course(session, course_id, book_id)
    if book.status != BookStatus.failed:
        raise HTTPException(409, RETRY_NOT_FAILED_MESSAGE)
    book.status = BookStatus.queued
    book.status_error = None
    await session.commit()
    await _defer_or_fail(session, book)
    return book


@router.get("/{book_id}/file")
async def get_book_file(
    course_id: uuid.UUID,
    book_id: uuid.UUID,
    _user: User = Depends(require_course_enrollment),
    session: AsyncSession = Depends(get_session),
) -> FileResponse:
    """Serve the PDF bytes for an enrolled user.

    Read-only (`require_course_enrollment`, not `require_course_owner_or_admin`;
    no CSRF header). `get_book_in_course` is the cross-course guard - a
    `book_id` that does not belong to `course_id` 404s before any file is
    touched. `FileResponse` (Starlette) natively handles `Range`/206/416/
    `ETag`/`Accept-Ranges` - no manual byte-range parsing is written here.
    `book.storage_path` is read server-side only and never serialized back
    to the client (no public/static file URL exists for PDFs).
    """
    book = await repo.get_book_in_course(session, course_id, book_id)
    if book.status != BookStatus.ready:
        raise HTTPException(404, BOOK_NOT_READY_MESSAGE)
    return FileResponse(book.storage_path, media_type="application/pdf")
