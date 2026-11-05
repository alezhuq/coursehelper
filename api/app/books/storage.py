"""Streaming upload validation + atomic storage writes.

`stream_upload_to_temp` reads an `UploadFile` in fixed-size chunks,
updating a running SHA-256 digest and byte total on every chunk, and
enforces the size cap on that *running total* - never on the client's
`Content-Length` header, which can be omitted (chunked transfer encoding)
or lied about. The PDF signature check runs against only the
first bytes of the stream (never trust the client's Content-Type
or filename extension).

`promote_temp_to_final` is the only place bytes move to their canonical
path, via `os.replace` - atomic on POSIX when source and destination share
a filesystem, so an interrupted upload never leaves a corrupt file at a
path other code treats as complete.
"""

import hashlib
import os
from pathlib import Path
from uuid import uuid4

from fastapi import HTTPException, UploadFile
from fastapi.concurrency import run_in_threadpool

CHUNK_SIZE = 1024 * 1024  # 1 MiB read chunks
PDF_SIGNATURE = b"%PDF-"
PDF_SIGNATURE_SCAN_WINDOW = 1024  # PDF spec permits the header within the first 1024 bytes

NON_PDF_MESSAGE = "This file is not a PDF. Only PDF files can be uploaded."
OVERSIZE_MESSAGE = "This file is larger than the 300 MB limit. Compress or split it and try again."


async def stream_upload_to_temp(
    upload: UploadFile, tmp_dir: Path, max_bytes: int
) -> tuple[Path, str, int]:
    """Stream `upload` to a temp file under `tmp_dir`.

    Returns `(temp_path, sha256_hex, size_bytes)`. Raises
    `HTTPException(413)` the moment the running byte count exceeds
    `max_bytes` (authoritative over `Content-Length`), and
    `HTTPException(422)` if the first `PDF_SIGNATURE_SCAN_WINDOW` bytes
    never contain the `%PDF-` signature. Both failure paths remove the
    temp file before raising - the caller does not need to clean up on
    these two errors, but still must remove the temp file in a `finally`
    on any *downstream* failure (e.g. a duplicate-checksum 409).
    """
    tmp_dir.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256()
    total = 0
    header_window = bytearray()
    tmp_path = tmp_dir / f"{uuid4()}.upload"

    fh = await run_in_threadpool(open, tmp_path, "wb")
    try:
        while chunk := await upload.read(CHUNK_SIZE):
            total += len(chunk)
            if total > max_bytes:
                await run_in_threadpool(fh.close)
                remove_quietly(tmp_path)
                raise HTTPException(413, OVERSIZE_MESSAGE)
            if len(header_window) < PDF_SIGNATURE_SCAN_WINDOW:
                header_window.extend(chunk[: PDF_SIGNATURE_SCAN_WINDOW - len(header_window)])
            digest.update(chunk)
            await run_in_threadpool(fh.write, chunk)
    finally:
        if not fh.closed:
            await run_in_threadpool(fh.close)

    if PDF_SIGNATURE not in bytes(header_window):
        remove_quietly(tmp_path)
        raise HTTPException(422, NON_PDF_MESSAGE)

    return tmp_path, digest.hexdigest(), total


def promote_temp_to_final(temp_path: Path, final_path: Path) -> None:
    """Atomically move a validated temp file into its canonical path.

    `final_path` is always server-generated - `{book_storage_dir}/
    {course_id}/{book_id}.pdf` - never built from the client filename;
    the route constructs it.
    """
    final_path.parent.mkdir(parents=True, exist_ok=True)
    os.replace(temp_path, final_path)


def remove_quietly(path: Path | str) -> None:
    """Best-effort delete - failed uploads must never leak disk space, but
    a missing/already-removed file is not itself an error."""
    try:
        os.remove(path)
    except OSError:
        pass
