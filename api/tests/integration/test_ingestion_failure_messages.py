"""Integration test: stage-tagged, human-readable failure messages (D-17,
ING-04) and the parse-stage wall-clock timeout.

The chunk and embed stages are monkeypatched inside `app.ingestion.pipeline`
(chunk_document / embed_batch) to raise, and `ingest_book_stub` must record a
`[stage]`-tagged `status_error` - proving the tagged `RuntimeError`s from
`run_pipeline` flow unchanged into its broad `except Exception` handler.

The parse stage runs in a child process (a thread cannot be killed, a process
can), so its failure modes are driven through the
`app.ingestion.pipeline._parse_child_argv` seam: a child that exits non-zero
with a stderr message, a child that hangs past the timeout (it must be killed
and reaped, never left running), and a child that SIGKILLs itself (the OOM
shape). The chunk/embed tests below still exercise the real child parse end
to end against `digital_sample.pdf`.
"""

import os
import sys
import time
from pathlib import Path

import pytest
from testcontainers.postgres import PostgresContainer

from app.books.models import Book, BookStatus
from app.courses.models import Course
from app.db.base import async_session_factory
from app.ingestion.tasks import ingest_book_stub
from app.settings import get_settings
from app.users.models import Role, User
from app.users.repo import upsert_user

FIXTURES_DIR = Path(__file__).resolve().parent.parent / "fixtures"
DIGITAL_SAMPLE = FIXTURES_DIR / "digital_sample.pdf"


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


async def _make_book(session, course: Course, owner: User, checksum: str) -> Book:
    book = Book(
        course_id=course.id,
        uploaded_by=owner.id,
        original_filename="digital_sample.pdf",
        storage_path=str(DIGITAL_SAMPLE),
        checksum_sha256=checksum,
        size_bytes=1024,
        status=BookStatus.queued,
    )
    session.add(book)
    await session.flush()
    return book


async def _seed_book(checksum: str, email: str, course_code: str):
    async with async_session_factory() as session:
        owner = await _make_user(session, email)
        course = await _make_course(session, owner, course_code)
        book = await _make_book(session, course, owner, checksum)
        await session.commit()
        return book.id


def _child_argv(script: str):
    """Build a `_parse_child_argv` replacement that runs `script` instead of
    the real Docling parse child."""

    def _argv(storage_path: str, out_path: str) -> list[str]:
        return [sys.executable, "-c", script]

    return _argv


async def test_parse_failure_is_tagged(
    postgres_container: PostgresContainer, monkeypatch: pytest.MonkeyPatch
) -> None:
    book_id = await _seed_book("1" * 64, "fail-prof-1@sfu.ca", "CMPT 430")

    monkeypatch.setattr(
        "app.ingestion.pipeline._parse_child_argv",
        _child_argv("import sys; sys.stderr.write('bad pdf bytes\\n'); sys.exit(1)"),
    )

    await ingest_book_stub(book_id=str(book_id))

    async with async_session_factory() as session:
        refreshed = await session.get(Book, book_id)
        assert refreshed is not None
        assert refreshed.status == BookStatus.failed
        assert refreshed.status_error is not None
        assert refreshed.status_error.startswith("[parse]")
        assert "bad pdf bytes" in refreshed.status_error


async def test_chunk_failure_is_tagged(
    postgres_container: PostgresContainer, monkeypatch: pytest.MonkeyPatch
) -> None:
    book_id = await _seed_book("2" * 64, "fail-prof-2@sfu.ca", "CMPT 431")

    def _boom(doc):
        raise RuntimeError("chunker exploded")

    monkeypatch.setattr("app.ingestion.pipeline.chunk_document", _boom)

    await ingest_book_stub(book_id=str(book_id))

    async with async_session_factory() as session:
        refreshed = await session.get(Book, book_id)
        assert refreshed is not None
        assert refreshed.status == BookStatus.failed
        assert refreshed.status_error is not None
        assert refreshed.status_error.startswith("[chunk]")
        assert "chunker exploded" in refreshed.status_error


async def test_embed_failure_is_tagged(
    postgres_container: PostgresContainer, monkeypatch: pytest.MonkeyPatch
) -> None:
    book_id = await _seed_book("3" * 64, "fail-prof-3@sfu.ca", "CMPT 432")

    def _boom(texts: list[str]):
        raise RuntimeError("embedding model unavailable")

    monkeypatch.setattr("app.ingestion.pipeline.embed_batch", _boom)

    await ingest_book_stub(book_id=str(book_id))

    async with async_session_factory() as session:
        refreshed = await session.get(Book, book_id)
        assert refreshed is not None
        assert refreshed.status == BookStatus.failed
        assert refreshed.status_error is not None
        assert refreshed.status_error.startswith("[embed]")
        assert "embedding model unavailable" in refreshed.status_error


async def test_parse_timeout_kills_and_reaps_the_child(
    postgres_container: PostgresContainer,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    book_id = await _seed_book("4" * 64, "fail-prof-4@sfu.ca", "CMPT 433")
    pid_file = tmp_path / "child.pid"

    monkeypatch.setenv("INGEST_PARSE_TIMEOUT_SECONDS", "2")
    get_settings.cache_clear()
    monkeypatch.setattr(
        "app.ingestion.pipeline._parse_child_argv",
        _child_argv(
            f"import os, time; open({str(pid_file)!r}, 'w').write(str(os.getpid())); time.sleep(60)"
        ),
    )

    started = time.monotonic()
    await ingest_book_stub(book_id=str(book_id))
    elapsed = time.monotonic() - started

    assert elapsed < 30, "the stage must not wait out the 60 s child"

    async with async_session_factory() as session:
        refreshed = await session.get(Book, book_id)
        assert refreshed is not None
        assert refreshed.status == BookStatus.failed
        assert refreshed.status_error is not None
        assert refreshed.status_error.startswith("[parse]")
        assert "timed out" in refreshed.status_error

    # The core regression: the timed-out parse is a dead, reaped process,
    # not a thread that keeps running in the background.
    child_pid = int(pid_file.read_text())
    with pytest.raises(ProcessLookupError):
        os.kill(child_pid, 0)

    get_settings.cache_clear()


async def test_killed_child_yields_readable_signal_failure(
    postgres_container: PostgresContainer, monkeypatch: pytest.MonkeyPatch
) -> None:
    book_id = await _seed_book("5" * 64, "fail-prof-5@sfu.ca", "CMPT 434")

    monkeypatch.setattr(
        "app.ingestion.pipeline._parse_child_argv",
        _child_argv("import os, signal; os.kill(os.getpid(), signal.SIGKILL)"),
    )

    await ingest_book_stub(book_id=str(book_id))

    async with async_session_factory() as session:
        refreshed = await session.get(Book, book_id)
        assert refreshed is not None
        assert refreshed.status == BookStatus.failed
        assert refreshed.status_error is not None
        assert refreshed.status_error.startswith("[parse]")
        assert "signal 9" in refreshed.status_error
