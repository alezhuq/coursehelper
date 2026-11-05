"""Unit tests for `select_sources_within_budget`: the pure function that caps
the retrieved chunks sent to the model at an env-configurable token budget.

Contract: walk rows in RRF order, always keep the top-ranked chunk, and stop
at the first row that would push the running total over the budget - never
skip ahead to a smaller lower-ranked chunk and never truncate chunk text.
"""

from types import SimpleNamespace

# `app.chat.routes` is imported lazily inside the wrapper below, never at module top:
# importing it at collection time creates the app.chat.* loggers before
# alembic's fileConfig (run by the first `postgres_container` fixture) can
# disable them, which silently breaks every caplog-based test that runs later.


def select_sources_within_budget(sources, budget_tokens):
    from app.chat.routes import select_sources_within_budget as _select

    return _select(sources, budget_tokens)


def _rows(*token_counts: int) -> list[SimpleNamespace]:
    return [SimpleNamespace(id=i, token_count=count) for i, count in enumerate(token_counts)]


def test_keeps_rows_that_fit_in_rrf_order() -> None:
    rows = _rows(500, 500, 500)
    kept = select_sources_within_budget(rows, 1200)
    assert [r.id for r in kept] == [0, 1]


def test_stops_at_first_overflow_and_never_skips_ahead() -> None:
    rows = _rows(500, 900, 100)
    kept = select_sources_within_budget(rows, 1200)
    assert [r.id for r in kept] == [0]


def test_top_ranked_chunk_is_always_kept_even_over_budget() -> None:
    rows = _rows(2000, 100)
    kept = select_sources_within_budget(rows, 1000)
    assert [r.id for r in kept] == [0]


def test_exact_fit_is_kept() -> None:
    rows = _rows(600, 600, 1)
    kept = select_sources_within_budget(rows, 1200)
    assert [r.id for r in kept] == [0, 1]


def test_empty_returns_empty() -> None:
    assert select_sources_within_budget([], 1000) == []
