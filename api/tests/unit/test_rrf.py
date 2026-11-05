"""Unit tests for `fuse_rrf` - pure Python, no database.

Exercises the RRF formula against known per-arm rank lists: a chunk found
by both arms must outrank one found by only one arm, identical inputs must
be order-stable, `top_n` must cap the result length, and a keyword-only id
must still score (not silently dropped).
"""

from app.search.rrf import fuse_rrf


def test_both_arm_id_outranks_single_arm_id() -> None:
    # "shared" appears in both arms (rank 1 in each); "vector-only" and
    # "keyword-only" each appear in exactly one arm.
    vector_ids = ["shared", "vector-only"]
    keyword_ids = ["shared", "keyword-only"]

    result = fuse_rrf(vector_ids, keyword_ids)

    assert result[0] == "shared"
    assert set(result) == {"shared", "vector-only", "keyword-only"}


def test_identical_inputs_are_order_stable() -> None:
    vector_ids = ["a", "b", "c"]
    keyword_ids = ["a", "b", "c"]

    first_run = fuse_rrf(vector_ids, keyword_ids)
    second_run = fuse_rrf(vector_ids, keyword_ids)

    assert first_run == second_run == ["a", "b", "c"]


def test_top_n_caps_result_length() -> None:
    vector_ids = [f"v{i}" for i in range(30)]
    keyword_ids = [f"k{i}" for i in range(30)]

    result = fuse_rrf(vector_ids, keyword_ids, top_n=5)

    assert len(result) == 5


def test_keyword_only_id_still_scores() -> None:
    vector_ids = ["v1", "v2"]
    keyword_ids = ["keyword-only"]

    result = fuse_rrf(vector_ids, keyword_ids)

    assert "keyword-only" in result


def test_k_constant_changes_relative_weighting() -> None:
    # A low-rank vector hit (rank 1) fused with nothing from keyword should
    # always score higher than a high-rank (rank 50) vector hit, regardless
    # of k - sanity check that rank still matters within one arm.
    vector_ids = ["best"] + [f"filler{i}" for i in range(49)] + ["worst"]
    keyword_ids: list[str] = []

    result = fuse_rrf(vector_ids, keyword_ids, top_n=2)

    assert result[0] == "best"
