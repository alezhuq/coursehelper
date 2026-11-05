"""Unit tests for deterministic chunk IDs and the no-commit
delete-then-insert contract of `replace_chunks_for_book`.

No database is needed for these: `chunk_id` is a pure function, and the
no-commit assertion reads `replace_chunks_for_book`'s own source rather
than exercising it against a real session (the real write path is
exercised against Postgres in `tests/integration/test_indexing.py`).
"""

import inspect
import uuid

from app.chunks.repo import CHUNK_ID_NAMESPACE, chunk_id, replace_chunks_for_book


def test_chunk_id_namespace_is_fixed() -> None:
    assert CHUNK_ID_NAMESPACE == uuid.UUID("6f2a1b9c-3d4e-5a6b-7c8d-9e0f1a2b3c4d")


def test_chunk_id_is_deterministic_across_calls() -> None:
    book_id = uuid.uuid4()
    first = chunk_id(book_id, 0)
    second = chunk_id(book_id, 0)
    assert first == second


def test_chunk_id_is_scoped_by_index() -> None:
    book_id = uuid.uuid4()
    assert chunk_id(book_id, 0) != chunk_id(book_id, 1)


def test_chunk_id_is_scoped_by_book() -> None:
    other_book_id = uuid.uuid4()
    book_id = uuid.uuid4()
    assert chunk_id(book_id, 0) != chunk_id(other_book_id, 0)


def test_replace_chunks_for_book_never_commits() -> None:
    source = inspect.getsource(replace_chunks_for_book)
    assert "session.commit(" not in source
