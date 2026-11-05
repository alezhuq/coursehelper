"""Integration test: table-aware, non-garbled, non-split chunking.

`digital_sample.pdf`'s Methods page (2) includes a simple data table
(Trial / Light Intensity / Oxygen Output). Docling's `do_table_structure`
recognizes the table's cell structure; `HybridChunker` treats a table as an
atomic document item that is never split mid-table across chunks. This test
proves both: the cell content is present (not garbled OCR/text soup) and
every row of the table lands together in exactly one chunk.
"""

from pathlib import Path

from app.ingestion.chunking import chunk_document
from app.ingestion.parsing import parse_book

FIXTURES_DIR = Path(__file__).resolve().parent.parent / "fixtures"
DIGITAL_SAMPLE = FIXTURES_DIR / "digital_sample.pdf"

# Every row's key values from the fixture's table (see make_fixtures.py):
# Trial | Light Intensity (lux) | Oxygen Output (mL)
TABLE_ROW_VALUES = ["500", "2.1", "1000", "4.3", "1500", "6.0", "2000", "6.1"]


def test_table_content_is_preserved_and_not_split_across_chunks() -> None:
    doc = parse_book(str(DIGITAL_SAMPLE))
    assert len(doc.tables) == 1, "fixture should parse to exactly one table"

    chunks = chunk_document(doc)
    assert chunks

    # Every table row value must be present, and all of them together in a
    # SINGLE chunk's text - proving the table was never split mid-table.
    chunks_containing_table_values = [
        c for c in chunks if all(value in c["text"] for value in TABLE_ROW_VALUES)
    ]
    assert len(chunks_containing_table_values) == 1, (
        "expected exactly one chunk to contain the full, unsplit table content; "
        f"got {len(chunks_containing_table_values)} matching chunks out of {len(chunks)}"
    )

    # Column headers are recognized (table structure, not garbled OCR text).
    table_chunk = chunks_containing_table_values[0]
    assert "Light Intensity" in table_chunk["text"]
    assert "Oxygen Output" in table_chunk["text"]
