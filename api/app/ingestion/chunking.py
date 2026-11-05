"""HybridChunker singleton and the chunk_document entrypoint (ING-02).

Mirrors `app/db/base.py`'s lazy-singleton pattern (see `app/ingestion/
parsing.py`): building a `HybridChunker` loads the bge-m3 HuggingFace
tokenizer, so it is built once per worker process, lazily, on first use, never
inside the per-task function body.

`chunk_document` chunks DIRECTLY from the in-memory `DoclingDocument`
produced by `app.ingestion.parsing.parse_book` - it never exports the
document to Markdown or plain text first. Round-tripping through a lossy
export format permanently destroys the `.prov`/page-number/bbox provenance
this function relies on to compute `page_start`/`page_end`.

Using bge-m3's own HuggingFace tokenizer (not `tiktoken`) for both the
`max_tokens=800` chunk ceiling and the reported `token_count` is required so
the ceiling matches what the embedding model will actually see.

`chunk_document` is a synchronous, CPU-bound call - callers must wrap it in
`asyncio.to_thread(...)`, same contract as `parse_book`/`embed_batch`.

docling_core/transformers are imported lazily, inside `_get_chunker`,
rather than at module top level: those packages only exist in the Docker
image's baked environment, not the host dev .venv, and this module is
imported transitively by `app.main` (via `app.ingestion.tasks` ->
`app.ingestion.pipeline`). A top-level import here would make importing
`app.main` itself require the ML stack, breaking host test collection.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from docling_core.transforms.chunker.hybrid_chunker import HybridChunker
    from docling_core.types.doc.document import DoclingDocument

_chunker: HybridChunker | None = None

MAX_TOKENS = 800


def _get_chunker() -> HybridChunker:
    global _chunker
    if _chunker is None:
        from docling_core.transforms.chunker.hybrid_chunker import HybridChunker
        from docling_core.transforms.chunker.tokenizer.huggingface import (
            HuggingFaceTokenizer,
        )
        from transformers import AutoTokenizer

        tokenizer = HuggingFaceTokenizer(
            tokenizer=AutoTokenizer.from_pretrained("BAAI/bge-m3"),
            max_tokens=MAX_TOKENS,
        )
        # merge_peers=True (the default): HybridChunker's second, token-aware
        # pass merges undersized sibling chunks under the same heading after
        # the initial structure-based split, in addition to only splitting
        # oversized chunks - never merging across a section/table/heading
        # boundary.
        _chunker = HybridChunker(tokenizer=tokenizer, merge_peers=True)
    return _chunker


def _page_range(chunk: Any) -> tuple[int, int]:
    pages = [prov.page_no for item in chunk.meta.doc_items for prov in item.prov]
    return (min(pages), max(pages)) if pages else (1, 1)


def chunk_document(doc: DoclingDocument) -> list[dict[str, Any]]:
    """Chunk `doc` into token-bounded, page-provenanced, heading-pathed
    records.

    Synchronous, CPU-bound - callers must wrap this in
    `asyncio.to_thread(...)`. Returns `[]` (never raises) when `doc` has no
    chunkable content.

    Each record:
      - chunk_index: enumeration order (0-based), used to derive the stable
        chunk_id (app/chunks/repo.py::chunk_id) and the
        UniqueConstraint(book_id, chunk_index) idempotency key.
      - text: raw chunk text, for storage + the tsvector full-text index.
      - embed_text: chunker.contextualize(chunk) - heading-enriched text, for
        the embedding model only (never stored as the display/search text).
      - page_start/page_end: physical page range from
        chunk.meta.doc_items[].prov[].page_no (1-indexed); defaults to (1, 1)
        only when a chunk has no provenance items at all (should not occur
        for real content).
      - section_path: ancestor heading list (chunk.meta.headings).
      - token_count: bge-m3 tokenizer count of the raw text (not
        len()/tiktoken) - matches the same tokenizer enforcing the
        max_tokens=800 ceiling above.
    """
    chunker = _get_chunker()
    records: list[dict[str, Any]] = []
    for index, chunk in enumerate(chunker.chunk(dl_doc=doc)):
        page_start, page_end = _page_range(chunk)
        records.append(
            {
                "chunk_index": index,
                "text": chunk.text,
                "embed_text": chunker.contextualize(chunk),
                "page_start": page_start,
                "page_end": page_end,
                "section_path": list(chunk.meta.headings or []),
                "token_count": chunker.tokenizer.count_tokens(chunk.text),
            }
        )
    return records
