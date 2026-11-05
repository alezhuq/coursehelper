"""Chunk persistence: deterministic IDs + delete-then-insert replace
(ING-02 stability, ING-04 idempotency).

`replace_chunks_for_book` never does a `SELECT`-then-diff or a
per-row upsert - it deletes every existing chunk row for the book and
inserts the fresh set, inside whatever transaction the caller (the
ingestion pipeline via `ingest_book_stub`) is already holding open. This
mirrors `app/books/repo.py::create_book`'s reasoning for never doing
check-then-act: a retried ingestion job (after a failed prior attempt, or
a re-ingestion of an updated PDF) must never duplicate chunks and must
never leave stale rows behind if the new chunk set is smaller than the
old one - a delete-then-insert in one transaction guarantees both, and
`uq_chunks_book_chunk_index` (0007_chunks migration) is the belt-and-
suspenders safety net if that transaction boundary is ever violated.

Chunk IDs are derived deterministically from `(book_id, chunk_index)` via
`uuid5` under a fixed, checked-in namespace, so the same logical chunk
always gets the same id across repeated ingestion runs of the same book -
this is what makes "the same PDF re-ingested produces the same chunk IDs"
true (ING-02), independent of whether the delete-then-insert idempotency
guard above ever triggers.
"""

import uuid

from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.chunks.models import Chunk

CHUNK_ID_NAMESPACE = uuid.UUID("6f2a1b9c-3d4e-5a6b-7c8d-9e0f1a2b3c4d")


def chunk_id(book_id: uuid.UUID, chunk_index: int) -> uuid.UUID:
    """Deterministic id for (book_id, chunk_index): same inputs always
    yield the same uuid5, across processes and across re-ingestion runs."""
    return uuid.uuid5(CHUNK_ID_NAMESPACE, f"{book_id}:{chunk_index}")


async def replace_chunks_for_book(
    session: AsyncSession,
    book_id: uuid.UUID,
    chunks: list[dict],
    embeddings: list[list[float]],
) -> None:
    """Delete all existing chunks for `book_id`, then insert the fresh set.

    Does NOT commit - the caller owns the single transaction (matching
    `app/books/repo.py::delete_book`'s no-commit contract), so this
    delete+insert is atomic with whatever else the caller does in the same
    transaction (e.g. setting `Book.status = ready`).

    `chunks` and `embeddings` are paired positionally via `zip(...,
    strict=True)`, so a length mismatch between the two raises instead of
    silently truncating or misaligning a chunk with the wrong vector.
    """
    await session.execute(delete(Chunk).where(Chunk.book_id == book_id))
    session.add_all(
        [
            Chunk(
                id=chunk_id(book_id, c["chunk_index"]),
                book_id=book_id,
                chunk_index=c["chunk_index"],
                text=c["text"],
                page_start=c["page_start"],
                page_end=c["page_end"],
                section_path=c["section_path"],
                token_count=c["token_count"],
                embedding=vec,
            )
            for c, vec in zip(chunks, embeddings, strict=True)
        ]
    )
