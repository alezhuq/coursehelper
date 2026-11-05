"""bge-m3 embedding-model singleton and the embed_batch entrypoint.

Mirrors `app/db/base.py`'s lazy-singleton pattern (see `app/ingestion/
parsing.py` for the same reasoning applied to Docling): loading
`SentenceTransformer("BAAI/bge-m3")` costs real seconds (a 567M-parameter
model), so it is built once per worker process, lazily, on first use - never
inside the per-task function body.

`embed_batch` is a synchronous, CPU-bound call - `model.encode()` blocks on
inference. Callers (the ingestion pipeline) MUST wrap it in
`asyncio.to_thread(...)` so it never blocks the worker's event loop.

Model weights load from the build-time-baked, enforced-offline HF cache
(HF_HUB_OFFLINE=1, api/Dockerfile) - no runtime network dependency.

`sentence_transformers` is imported lazily, inside `_get_model`, rather
than at module top level: it only exists in the Docker image's baked
environment, not the host dev .venv, and this module is imported
transitively by `app.main` (via `app.ingestion.tasks` ->
`app.ingestion.pipeline`). A top-level import here would make importing
`app.main` itself require the ML stack, breaking host test collection.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from sentence_transformers import SentenceTransformer

_model: SentenceTransformer | None = None


def _get_model() -> SentenceTransformer:
    global _model
    if _model is None:
        from sentence_transformers import SentenceTransformer

        _model = SentenceTransformer("BAAI/bge-m3")
    return _model


def embed_batch(texts: list[str]) -> list[list[float]]:
    """Encode `texts` into 1024-dim, L2-normalized bge-m3 embeddings.

    Synchronous, CPU-bound - callers must wrap this in
    `asyncio.to_thread(...)`. `normalize_embeddings=True` is required so
    downstream cosine-similarity search (pgvector HNSW) is a plain
    dot product over unit vectors. `batch_size=4` is a conservative default
    for CPU-only inference; tune upward if a load test shows headroom.
    """
    vectors = _get_model().encode(texts, normalize_embeddings=True, batch_size=4)
    return vectors.tolist()
