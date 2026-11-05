"""Integration test: born-digital PDF parse + chunk (ING-01 page-number edge).

Exercises the real Docling pipeline against `digital_sample.pdf` (a
born-digital, no-OCR-needed fixture spanning 3 physical pages) and proves
chunk page ranges trace to the physical page the content was authored on,
and that no OCR was needed to extract the (exact, non-garbled) text.
"""

from pathlib import Path

from app.ingestion.chunking import chunk_document
from app.ingestion.parsing import parse_book

FIXTURES_DIR = Path(__file__).resolve().parent.parent / "fixtures"
DIGITAL_SAMPLE = FIXTURES_DIR / "digital_sample.pdf"


def test_digital_pdf_yields_correct_page_numbers_and_no_ocr_needed() -> None:
    doc = parse_book(str(DIGITAL_SAMPLE))
    chunks = chunk_document(doc)
    assert chunks, "expected at least one chunk from the digital sample"

    # Page 1: the Chapter 1 introduction/photosynthesis content.
    page_1_chunks = [c for c in chunks if c["page_start"] <= 1 <= c["page_end"]]
    assert page_1_chunks
    assert any("Photosynthesis" in c["text"] for c in page_1_chunks)

    # Page 2: Methods content (authored after the first PageBreak).
    page_2_chunks = [c for c in chunks if c["page_start"] <= 2 <= c["page_end"]]
    assert page_2_chunks
    assert any("oxygen output" in c["text"].lower() for c in page_2_chunks)

    # Page 3: Results content (authored after the second PageBreak).
    page_3_chunks = [c for c in chunks if c["page_start"] <= 3 <= c["page_end"]]
    assert page_3_chunks
    assert any("results confirmed" in c["text"].lower() for c in page_3_chunks)

    # Born-digital text is extracted exactly (no OCR artifacts/garbling) -
    # the source paragraph text appears verbatim, character for character.
    assert any(
        "Photosynthesis is the process by which green plants, algae, and some "
        "bacteria convert light energy" in c["text"]
        for c in page_1_chunks
    )


async def test_subprocess_parse_round_trip_preserves_chunk_provenance() -> None:
    """The child-process parse hands the document back as lossless
    DoclingDocument JSON (never Markdown), so chunking it yields exactly what
    chunking the in-process parse yields: text, page range, section path and
    token count all survive the round trip."""
    from app.ingestion.pipeline import parse_book_in_subprocess

    in_process = chunk_document(parse_book(str(DIGITAL_SAMPLE)))
    via_child = chunk_document(await parse_book_in_subprocess(str(DIGITAL_SAMPLE), 900))

    def _key(chunks):
        return [
            (c["text"], c["page_start"], c["page_end"], c["section_path"], c["token_count"])
            for c in chunks
        ]

    assert via_child
    assert _key(via_child) == _key(in_process)
