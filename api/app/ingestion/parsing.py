"""Docling document-converter singleton and the parse_book entrypoint.

Mirrors `app/db/base.py`'s lazy-singleton pattern: `DocumentConverter`
construction loads Docling's layout model plus the configured OCR backend,
which costs real seconds-to-tens-of-seconds. Building it fresh inside every
ingestion task call (rather than once per worker process) would multiply
that cost by every queued book, so `_get_converter`
builds it once, lazily, on first use, and caches it at module scope.

`parse_book` is a synchronous, CPU-bound call - `converter.convert()` blocks
on layout detection, OCR, and table-structure inference. In production it
runs inside the parse child process (`app.ingestion.parse_child`, spawned by
`app.ingestion.pipeline.parse_book_in_subprocess`), never in the worker's own
process: a thread cannot be killed on a timeout, a child process can, and an
OOM in the child no longer takes the worker down. The converter singleton
below therefore lives per child process, which costs one model load per book
- accepted in exchange for killability and for the memory being returned to
the OS when the child exits.

Model/OCR weights load from the build-time-baked, enforced-offline cache
(HF_HUB_OFFLINE=1, set in api/Dockerfile after the build-time warm-up)
- there is no runtime network dependency. A missing/renamed weight
fails fast at first use with a readable error instead of hanging on a
silent fetch.

Docling/docling_core are imported lazily, inside `_get_converter`/
`parse_book`, rather than at module top level: those packages only exist
in the Docker image's baked environment, not the host dev .venv, and this
module is imported transitively by `app.main` (via `app.ingestion.tasks` ->
`app.ingestion.pipeline`). A top-level import here would make importing
`app.main` itself require the ML stack, breaking host test collection.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from app.settings import get_settings

if TYPE_CHECKING:
    from docling.document_converter import DocumentConverter
    from docling_core.types.doc.document import DoclingDocument

_converter: DocumentConverter | None = None


def _get_converter() -> DocumentConverter:
    global _converter
    if _converter is None:
        from docling.datamodel.base_models import InputFormat
        from docling.datamodel.pipeline_options import (
            PdfPipelineOptions,
            RapidOcrOptions,
        )
        from docling.document_converter import DocumentConverter, PdfFormatOption

        ocr_backend = get_settings().ocr_backend
        if ocr_backend == "rapidocr":
            ocr_options = RapidOcrOptions()
        else:
            # Tesseract escape hatch: only reachable if
            # OCR_BACKEND is switched away from the "rapidocr" default AND
            # api/Dockerfile's commented Tesseract apt-get block is
            # uncommented + the image rebuilt. RapidOCR remains the default
            # because it needs no system packages.
            from docling.datamodel.pipeline_options import TesseractOcrOptions

            ocr_options = TesseractOcrOptions()

        pipeline_options = PdfPipelineOptions(
            # do_ocr=True enables Docling's automatic per-region
            # bitmap-coverage detection: born-digital pages/regions with a
            # clean extracted text layer are never OCR'd, only detected
            # scanned/bitmap regions are. Do NOT set force_full_page_ocr=True
            # here - that forces OCR on every page (including clean
            # born-digital ones), wasting CPU and risking OCR-introduced
            # errors on text that was already correct.
            do_ocr=True,
            ocr_options=ocr_options,
            do_table_structure=True,
        )
        _converter = DocumentConverter(
            format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=pipeline_options)}
        )
    return _converter


def parse_book(storage_path: str) -> DoclingDocument:
    """Parse a PDF at `storage_path` into a `DoclingDocument`.

    Synchronous, CPU-bound - callers must wrap this in
    `asyncio.to_thread(...)`. Returns the in-memory `DoclingDocument`
    directly; never export it to Markdown/plain text before further
    processing, doing so destroys page-number/bbox provenance.
    """
    return _get_converter().convert(storage_path).document
