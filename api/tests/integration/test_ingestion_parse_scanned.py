"""Integration test: scanned (image-only) PDF triggers automatic OCR (ING-01
OCR-trigger edge).

`scanned_sample.pdf` has NO extractable text layer at all (see
`make_fixtures.py`: the entire page is a single embedded raster image, no
reportlab text objects). Docling's `do_ocr=True` must automatically detect
the bitmap region and recognize its text with no manual intervention -
proving the automatic-OCR path (not a manual/forced OCR flag) actually ran.
"""

from pathlib import Path

from app.ingestion.chunking import chunk_document
from app.ingestion.parsing import parse_book
from tests.fixtures.make_fixtures import SCANNED_PAGE_KEYWORDS

FIXTURES_DIR = Path(__file__).resolve().parent.parent / "fixtures"
SCANNED_SAMPLE = FIXTURES_DIR / "scanned_sample.pdf"


def test_scanned_pdf_is_automatically_ocrd_into_usable_text() -> None:
    doc = parse_book(str(SCANNED_SAMPLE))
    chunks = chunk_document(doc)
    assert chunks, "expected OCR to recover at least one chunk of text"

    recognized_text = " ".join(c["text"] for c in chunks).lower()
    assert recognized_text.strip(), "OCR produced only whitespace"

    # Tolerant of minor OCR variance: require most (not necessarily all) of
    # the known keywords baked into the scanned image to be recognized.
    matched = [kw for kw in SCANNED_PAGE_KEYWORDS if kw in recognized_text]
    assert len(matched) >= len(SCANNED_PAGE_KEYWORDS) - 1, (
        f"expected most of {SCANNED_PAGE_KEYWORDS} in OCR'd text, got: {recognized_text!r}"
    )

    # Page provenance still resolves for an OCR'd chunk (single-page fixture).
    assert all(c["page_start"] == 1 and c["page_end"] == 1 for c in chunks)
