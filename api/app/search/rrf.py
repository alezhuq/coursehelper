"""Reciprocal Rank Fusion (RRF) math for hybrid search.

Pure Python, no DB imports - this stays unit-testable with no database
(`api/tests/unit/test_rrf.py`). `RRF_K` and `CANDIDATE_DEPTH` are the two
tunable constants: `k=60` is the Cormack et al. 2009 empirical default
(performs comparably across `k` in [40, 80]); `CANDIDATE_DEPTH=60` is kept
comfortably above pgvector HNSW's default `ef_search=40` so the vector arm
has recall headroom even before the iterative-scan fix in
`app/search/repo.py::vector_arm`.
"""

RRF_K = 60
CANDIDATE_DEPTH = 60


def fuse_rrf(
    vector_ids: list[str],
    keyword_ids: list[str],
    k: int = RRF_K,
    top_n: int = 20,
) -> list[str]:
    """Fuse two per-arm ranked id lists into one top_n ranking.

    Score per id = sum of 1/(k + rank) over every arm the id appears in
    (rank is 1-indexed within that arm). An id present in only one arm
    still scores - just lower than one found by both. Ties keep the
    relative order `sorted` (stable sort) produces from insertion order
    (vector arm ids inserted first, then keyword arm ids), so results are
    deterministic given identical inputs.
    """
    scores: dict[str, float] = {}
    for rank, chunk_id in enumerate(vector_ids, start=1):
        scores[chunk_id] = scores.get(chunk_id, 0.0) + 1.0 / (k + rank)
    for rank, chunk_id in enumerate(keyword_ids, start=1):
        scores[chunk_id] = scores.get(chunk_id, 0.0) + 1.0 / (k + rank)
    ranked = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
    return [chunk_id for chunk_id, _ in ranked[:top_n]]
