"""Ingestion pipeline orchestrator (ING-01..ING-04): parse -> chunk -> embed
-> write+index, run in-process as four stages inside the one transaction
`ingest_book_stub` already owns.

`run_pipeline` is the only thing `_run_ingestion_stages` (app/ingestion/
tasks.py) delegates to - it is not itself a procrastinate task and never
chains four separate deferred jobs (one task, in-process
stages): chaining would require persisting the
intermediate `DoclingDocument` between jobs for no benefit at this scale.

The CPU-bound chunk and embed stages wrap their synchronous call in
`asyncio.to_thread(...)` so they never block the worker's event loop. Every
stage failure is re-raised as a `RuntimeError`
tagged with the stage name (`[parse]`, `[chunk]`, `[embed]`, `[write]`).
That tagged message flows unchanged into `ingest_book_stub`'s existing
`except Exception as exc: book.status_error = str(exc)[:2000]` handler,
giving ING-04's "human-readable, stage-tagged error".

The parse stage does NOT run in a thread: a thread cannot be killed, but a
child process can. `parse_book_in_subprocess` runs Docling in
`python -m app.ingestion.parse_child`, bounded by
`get_settings().ingest_parse_timeout_seconds`. On a timeout the child is
killed and reaped, so a pathological PDF becomes a readable `[parse]` timeout
failure instead of a runaway parse competing with the next book; an OOM kill of
the child becomes a readable `[parse]` failure
instead of killing the worker. The parent awaits the child's exit before
returning or raising, so the worker never runs two Docling parses at once and
`--concurrency 1` holds for the CPU-heavy stage. The document comes
back as lossless DoclingDocument JSON (prov/page numbers intact), never
Markdown or plain text.

Stage 4 (write+index) calls `replace_chunks_for_book`, which does not
commit - `ingest_book_stub` commits once, after `run_pipeline` returns,
matching the existing status-transition commit pattern. A zero-chunk
document is not an error: Stage 3 skips the embed call entirely when there
are no chunks (an empty batch has nothing to embed), and Stage 4 still runs
(deleting any prior chunks, inserting none) so the book resolves to `ready`
with zero chunks rather than raising (edge ING-02/empty, ING-03/empty).
"""

import asyncio
import contextlib
import logging
import os
import signal
import sys
import tempfile
from collections import deque
from pathlib import Path
from typing import TYPE_CHECKING

from sqlalchemy.ext.asyncio import AsyncSession

from app.books.models import Book
from app.chunks.repo import replace_chunks_for_book
from app.ingestion.chunking import chunk_document
from app.ingestion.embedding import embed_batch
from app.settings import get_settings

if TYPE_CHECKING:
    from docling_core.types.doc.document import DoclingDocument

logger = logging.getLogger(__name__)

# Root that contains the `app` package this module was imported from. Put on
# the child's PYTHONPATH so it imports exactly the parent's code: baked
# site-packages in production, the ./api/app mount in dev, /src in tests.
_PACKAGE_ROOT = Path(__file__).resolve().parents[2]


def _parse_child_argv(storage_path: str, out_path: str) -> list[str]:
    """Argv for the parse child. A module-level function so tests can
    replace it with a stand-in child (hang, crash, SIGKILL)."""
    return [sys.executable, "-m", "app.ingestion.parse_child", storage_path, out_path]


async def _drain_stderr(stream: asyncio.StreamReader, tail: deque[str]) -> None:
    """Forward the child's stderr (Docling progress) to the worker log and
    keep the last few lines for the failure message."""
    while True:
        line = await stream.readline()
        if not line:
            return
        text = line.decode(errors="replace").rstrip()
        if text:
            logger.info("parse child: %s", text)
            tail.append(text)


async def parse_book_in_subprocess(storage_path: str, timeout_seconds: float) -> "DoclingDocument":
    """Parse the PDF in a killable child process and return the document.

    Raises `RuntimeError` on a timeout, a signal death (OOM kill is signal
    9), or a non-zero exit. Never returns or raises before the child has
    exited, so two parses can never overlap.
    """
    fd, out_path = tempfile.mkstemp(suffix=".json")
    os.close(fd)

    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join(
        part for part in (str(_PACKAGE_ROOT), env.get("PYTHONPATH", "")) if part
    )

    tail: deque[str] = deque(maxlen=20)
    proc = await asyncio.create_subprocess_exec(
        *_parse_child_argv(storage_path, out_path),
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.PIPE,
        env=env,
    )
    assert proc.stderr is not None
    drain = asyncio.create_task(_drain_stderr(proc.stderr, tail))
    try:
        try:
            await asyncio.wait_for(proc.wait(), timeout_seconds)
        except TimeoutError:
            proc.kill()
            await proc.wait()
            raise RuntimeError(f"timed out after {timeout_seconds:g} seconds") from None

        await drain
        code = proc.returncode
        if code is not None and code < 0:
            name = signal.Signals(-code).name
            hint = " (likely out of memory)" if -code == signal.SIGKILL else ""
            raise RuntimeError(f"parser process killed by signal {-code} ({name}){hint}")
        if code:
            last = next((line for line in reversed(tail) if line.strip()), "")
            raise RuntimeError(last or f"parser process exited with code {code}")

        from docling_core.types.doc.document import DoclingDocument

        return await asyncio.to_thread(DoclingDocument.load_from_json, out_path)
    finally:
        # Cancellation (worker shutdown, job abort) lands here with the child
        # possibly still running: never leave it behind.
        if proc.returncode is None:
            with contextlib.suppress(ProcessLookupError):
                proc.kill()
            await proc.wait()
        if not drain.done():
            await drain
        Path(out_path).unlink(missing_ok=True)


async def run_pipeline(session: AsyncSession, book: Book) -> None:
    """Run parse -> chunk -> embed -> write+index for `book`.

    Raises a stage-tagged `RuntimeError` (`[parse]`/`[chunk]`/`[embed]`/
    `[write]`) on any stage failure, including a parse-stage timeout. Never
    commits - the caller (`ingest_book_stub`) owns the transaction.
    """
    try:
        doc = await parse_book_in_subprocess(
            book.storage_path, get_settings().ingest_parse_timeout_seconds
        )
    except Exception as exc:
        raise RuntimeError(f"[parse] {exc}") from exc

    try:
        chunks = await asyncio.to_thread(chunk_document, doc)
    except Exception as exc:
        raise RuntimeError(f"[chunk] {exc}") from exc

    try:
        if chunks:
            embeddings = await asyncio.to_thread(embed_batch, [c["embed_text"] for c in chunks])
        else:
            # No chunkable content (edge ING-02/empty): nothing to embed,
            # and calling embed_batch([]) would be a pointless model call.
            embeddings = []
    except Exception as exc:
        raise RuntimeError(f"[embed] {exc}") from exc

    try:
        await replace_chunks_for_book(session, book.id, chunks, embeddings)
    except Exception as exc:
        raise RuntimeError(f"[write] {exc}") from exc
