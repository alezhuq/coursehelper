"""Unit tests for `app.ingestion.chunking.chunk_document`.

Exercises the real Docling parse -> HybridChunker pipeline against the
`digital_sample.pdf` fixture (born-digital, no OCR needed) to prove token
bounds, page-range provenance, section heading paths, the raw/embed text
split, and chunk-ordering determinism across repeated runs.
"""

from pathlib import Path

from transformers import AutoTokenizer

from app.ingestion.chunking import chunk_document
from app.ingestion.parsing import parse_book

FIXTURES_DIR = Path(__file__).resolve().parent.parent / "fixtures"
DIGITAL_SAMPLE = FIXTURES_DIR / "digital_sample.pdf"

_tokenizer = AutoTokenizer.from_pretrained("BAAI/bge-m3")


def _bge_token_count(text: str) -> int:
    return len(_tokenizer.encode(text, add_special_tokens=False))


def test_chunks_respect_token_ceiling() -> None:
    doc = parse_book(str(DIGITAL_SAMPLE))
    chunks = chunk_document(doc)
    assert len(chunks) > 0
    for chunk in chunks:
        assert _bge_token_count(chunk["text"]) <= 800
        assert chunk["token_count"] <= 800


def test_chunks_have_valid_page_ranges() -> None:
    doc = parse_book(str(DIGITAL_SAMPLE))
    chunks = chunk_document(doc)
    for chunk in chunks:
        assert chunk["page_start"] >= 1
        assert chunk["page_end"] >= chunk["page_start"]
    # The fixture spans 3 physical pages (Chapter 1 intro, Methods+table,
    # Results); at least one chunk should trace to a page beyond the first.
    assert any(chunk["page_start"] > 1 or chunk["page_end"] > 1 for chunk in chunks)


def test_chunks_carry_section_heading_paths() -> None:
    doc = parse_book(str(DIGITAL_SAMPLE))
    chunks = chunk_document(doc)
    headed_chunks = [c for c in chunks if c["section_path"]]
    assert headed_chunks, "expected at least one chunk with a non-empty section_path"
    joined_headings = " ".join(h for c in headed_chunks for h in c["section_path"])
    assert "Methods" in joined_headings or "Results" in joined_headings


def test_embed_text_differs_from_raw_text_when_headings_present() -> None:
    doc = parse_book(str(DIGITAL_SAMPLE))
    chunks = chunk_document(doc)
    headed_chunks = [c for c in chunks if c["section_path"]]
    assert headed_chunks
    for chunk in headed_chunks:
        assert chunk["embed_text"] != chunk["text"]
        # The heading text should appear in embed_text (contextualize
        # prefixes headings) but not necessarily in the raw text.
        heading = chunk["section_path"][-1]
        assert heading in chunk["embed_text"]


def test_chunk_document_is_deterministic() -> None:
    doc = parse_book(str(DIGITAL_SAMPLE))
    chunks_a = chunk_document(doc)
    chunks_b = chunk_document(doc)
    assert [c["chunk_index"] for c in chunks_a] == [c["chunk_index"] for c in chunks_b]
    assert [c["text"] for c in chunks_a] == [c["text"] for c in chunks_b]


def test_chunk_document_returns_empty_list_for_no_extractable_text() -> None:
    doc = parse_book(str(DIGITAL_SAMPLE))
    # Simulate a document with no chunkable text items by clearing the body's
    # children - chunk_document must return [] rather than raising.
    empty_doc = doc.model_copy(deep=True)
    empty_doc.texts = []
    empty_doc.tables = []
    empty_doc.pictures = []
    empty_doc.body.children = []
    chunks = chunk_document(empty_doc)
    assert chunks == []
