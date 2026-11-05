"""Book persistence: create (checksum-dedupe via IntegrityError), fetch
scoped to a course, list, delete.

`create_book` never does a `SELECT`-then-`INSERT` for dedupe - two
concurrent uploads of the identical file race on the
`uq_books_course_checksum` unique constraint itself, and the loser's
`IntegrityError` becomes a 409.

`get_book_in_course` is the cross-course guard: a book id from
another course, or one that does not exist at all, is indistinguishable -
both are a 404. No function in this module accepts a bare `book_id`
without a `course_id` to scope it against, matching `app/authz/
dependencies.py`'s structural (user, course_id) constraint.

Follows this codebase's convention (`app/roles/service.py`,
`app/courses/repo.py`) of raising `HTTPException` from the repo layer,
keeping routes thin.
"""

import uuid

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.books.models import Book, BookStatus

DUPLICATE_CHECKSUM_MESSAGE = "This PDF has already been uploaded to this course."
BOOK_NOT_FOUND_MESSAGE = "Book not found"


async def create_book(
    session: AsyncSession,
    *,
    id: uuid.UUID,
    course_id: uuid.UUID,
    uploaded_by: uuid.UUID,
    filename: str,
    storage_path: str,
    checksum: str,
    size: int,
) -> Book:
    book = Book(
        id=id,
        course_id=course_id,
        uploaded_by=uploaded_by,
        original_filename=filename,
        storage_path=storage_path,
        checksum_sha256=checksum,
        size_bytes=size,
        status=BookStatus.queued,
    )
    session.add(book)
    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        # asyncpg surfaces the violated constraint name on the original
        # UniqueViolationError - check that rather than string-matching
        # the error text, which is driver/locale-fragile. SQLAlchemy's
        # asyncpg dialect wraps the real asyncpg exception in a DBAPI
        # adapter (`exc.orig`) and re-raises it `from` the original, so the
        # attribute lives one level down via `__cause__` when the DBAPI
        # wrapper itself doesn't carry it.
        orig = getattr(exc, "orig", None)
        constraint_name = getattr(orig, "constraint_name", None) or getattr(
            getattr(orig, "__cause__", None), "constraint_name", None
        )
        if constraint_name == "uq_books_course_checksum":
            raise HTTPException(409, DUPLICATE_CHECKSUM_MESSAGE) from exc
        raise
    return book


async def get_book_in_course(
    session: AsyncSession, course_id: uuid.UUID, book_id: uuid.UUID
) -> Book:
    """Fetch a book scoped to `course_id`; 404 if it does not exist or its
    `course_id` does not match the path (cross-course guard)."""
    result = await session.execute(select(Book).where(Book.id == book_id))
    book = result.scalar_one_or_none()
    if book is None or book.course_id != course_id:
        raise HTTPException(404, BOOK_NOT_FOUND_MESSAGE)
    return book


async def list_books_for_course(session: AsyncSession, course_id: uuid.UUID) -> list[Book]:
    result = await session.execute(
        select(Book).where(Book.course_id == course_id).order_by(Book.created_at.desc())
    )
    return list(result.scalars().all())


async def delete_book(session: AsyncSession, book: Book) -> None:
    await session.delete(book)
