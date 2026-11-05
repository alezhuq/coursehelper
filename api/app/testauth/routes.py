"""Playwright E2E login bypass plus book/chunk seed helpers.

Mints a session cookie directly for a test user, skipping the real
CAS/magic-link flow so the E2E suite doesn't need a live/faked CAS server or
an SMTP round trip for every test run. `seed_book`/`seed_chunks` let the
viewer/search E2E specs place a `ready` book with real chunks
directly, bypassing the upload validation/queue/real-ingestion-worker path
entirely - real ingestion takes 20-45 minutes (measured in a load
test) and is infeasible to run inside an E2E suite.

Security: this router is only ever registered by `main.py` when
`settings.enable_test_auth` is true, and `main.py` additionally refuses to
start when that flag is set while ENVIRONMENT is "production" (defense in
depth - a misconfigured prod container fails loudly instead of serving an
auth bypass). Production images must still build/deploy with
`ENABLE_TEST_AUTH` unset (see docker-compose.yml, which never sets it for
the `api` service outside the dev override). Like `/test-auth/login`,
`seed_book`/`seed_chunks` require no CSRF header and no enrollment check -
they carry the exact same test-only trust level as the login bypass itself,
never reachable outside a dev/E2E container.
"""

import hashlib
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, Form, HTTPException, UploadFile
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.responses import JSONResponse

from app.auth.session import set_session_cookie
from app.books.models import Book, BookStatus
from app.books.storage import PDF_SIGNATURE
from app.chunks.models import Chunk
from app.chunks.repo import chunk_id
from app.courses.models import Course
from app.db.base import get_session
from app.settings import get_settings
from app.users import repo as users_repo
from app.users.models import Role

router = APIRouter(prefix="/test-auth", tags=["test-auth"])


class TestLoginBody(BaseModel):
    computing_id: str
    role: Role = Role.student


@router.post("/login")
async def login(
    body: TestLoginBody,
    session: AsyncSession = Depends(get_session),
) -> JSONResponse:
    user = await users_repo.upsert_user(
        session,
        email=f"{body.computing_id}@sfu.ca",
        computing_id=body.computing_id,
        auth_method="cas",
        display_name=body.computing_id,
    )
    if user.role != body.role:
        user.role = body.role
    await session.commit()

    response = JSONResponse(content={"status": "ok"})
    set_session_cookie(response, user.id)
    return response


@router.post("/seed-book")
async def seed_book(
    file: UploadFile,
    course_id: str = Form(...),
    session: AsyncSession = Depends(get_session),
) -> JSONResponse:
    """Write `file`'s bytes to the book's canonical storage path and create
    a `Book` row already in `ready` status - no queue, no worker, no real
    Docling/bge-m3 pass. `uploaded_by` is the course's own owner (avoids a
    second test-only parameter); the caller only needs a `course_id` it
    already created via the real `POST /courses` route."""
    body = await file.read()
    if PDF_SIGNATURE not in body[:1024]:
        raise HTTPException(422, "Not a PDF")

    course = await session.get(Course, uuid.UUID(course_id))
    if course is None:
        raise HTTPException(404, "Course not found")

    book_id = uuid.uuid4()
    final_path = Path(get_settings().book_storage_dir) / course_id / f"{book_id}.pdf"

    def _write() -> None:
        final_path.parent.mkdir(parents=True, exist_ok=True)
        final_path.write_bytes(body)

    await run_in_threadpool(_write)

    book = Book(
        id=book_id,
        course_id=course.id,
        uploaded_by=course.owner_id,
        original_filename=file.filename or "seed.pdf",
        storage_path=str(final_path),
        checksum_sha256=hashlib.sha256(body).hexdigest(),
        size_bytes=len(body),
        status=BookStatus.ready,
    )
    session.add(book)
    await session.commit()
    return JSONResponse({"id": str(book.id)})


class SeedChunkBody(BaseModel):
    text: str
    page_start: int
    page_end: int
    section_path: list[str] = []


class SeedChunksBody(BaseModel):
    book_id: str
    chunks: list[SeedChunkBody]


@router.post("/seed-chunks")
async def seed_chunks(
    body: SeedChunksBody,
    session: AsyncSession = Depends(get_session),
) -> JSONResponse:
    """Insert real bge-m3-embedded `Chunk` rows for an already-seeded book
    (see `seed_book`), so `search.spec.ts` can exercise the
    real hybrid-search endpoint without waiting on the real ingestion
    pipeline. Embeds via the same `embed_batch` singleton production
    ingestion uses (`app.ingestion.embedding`) - imported lazily here, same
    reasoning as `app.ingestion.parsing`/`embedding` themselves: the ML
    stack only exists in the built Docker image, never the host dev venv."""
    from app.ingestion.embedding import embed_batch

    book_uuid = uuid.UUID(body.book_id)
    texts = [c.text for c in body.chunks]
    embeddings = await run_in_threadpool(embed_batch, texts) if texts else []

    session.add_all(
        [
            Chunk(
                id=chunk_id(book_uuid, index),
                book_id=book_uuid,
                chunk_index=index,
                text=chunk.text,
                page_start=chunk.page_start,
                page_end=chunk.page_end,
                section_path=chunk.section_path,
                token_count=len(chunk.text.split()),
                embedding=vector,
            )
            for index, (chunk, vector) in enumerate(zip(body.chunks, embeddings, strict=True))
        ]
    )
    await session.commit()
    return JSONResponse({"status": "ok"})
