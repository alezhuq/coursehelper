"""Hybrid search retrieval: two independent arms fused with RRF.

Two-arm design, not a single multi-CTE raw-SQL
query: the vector arm reuses the already-proven `Chunk.embedding.
cosine_distance(...)` comparator (`api/tests/integration/test_indexing.py`
line 114); the keyword arm uses `func.websearch_to_tsquery`/`func.
ts_rank_cd` through SQLAlchemy's `func` proxy. Every query string reaches
Postgres as a bound parameter - never string-interpolated into raw SQL. Both
arms scope via a correlated `book_id IN (SELECT id FROM
books WHERE course_id = ...)` subquery (never fetching a course's book ids
into Python first), matching `get_book_in_course`'s cross-course-guard
convention in `app/books/repo.py`.

`vector_arm` sets `hnsw.iterative_scan = relaxed_order` for its own
statement only (`SET LOCAL`, scoped to the current transaction) - without it, a
selective course-scoping filter can starve HNSW's fixed
`ef_search` candidate budget and return far fewer than `CANDIDATE_DEPTH`
rows. `relaxed_order` (not `strict_order`) is the faster iterative mode
since RRF re-ranks everything anyway - within-arm order doesn't need to
survive the index scan itself.

`fetch_display` re-sorts its result rows in Python by `fused_ids` order
(SQL `IN`/`ANY` is a set-membership test, not an ordering
directive) and calls `ts_headline` unconditionally with the same tsquery
used for retrieval - Postgres's own documented no-match fallback ("a single
fragment of the first MinWords words") IS the semantic-only-hit
snippet, no bespoke branch needed.

`fetch_display` also selects each chunk's full `text` and `token_count`.
The search route serializes only the `SearchResult` fields (the `snippet`
highlight), ignoring the extra row attributes; chat grounds the model on the
full stored `text` (a 2x35-word highlight is not enough context to answer
from) and budgets the prompt with `token_count`.
"""

from sqlalchemy import func, select, text
from sqlalchemy.engine import Row
from sqlalchemy.ext.asyncio import AsyncSession

from app.books.models import Book
from app.chunks.models import Chunk
from app.search.rrf import CANDIDATE_DEPTH

# \x01/\x02 (non-printable control chars) can never collide with characters
# that legitimately occur in OCR'd/parsed chunk text (unlike e.g. "**" or
# "<<"), so the frontend's StartSel/StopSel parser (highlightSnippet.tsx)
# never misfires on real content.
HEADLINE_OPTIONS = "StartSel=\x01, StopSel=\x02, MaxFragments=2, MinWords=15, MaxWords=35"


async def vector_arm(session: AsyncSession, course_id, query_vec: list[float]) -> list[str]:
    """Return up to `CANDIDATE_DEPTH` chunk ids ordered by cosine distance
    to `query_vec`, scoped to `course_id`'s books only."""
    await session.execute(text("SET LOCAL hnsw.iterative_scan = relaxed_order"))
    book_ids = select(Book.id).where(Book.course_id == course_id)
    stmt = (
        select(Chunk.id)
        .where(Chunk.book_id.in_(book_ids))
        .order_by(Chunk.embedding.cosine_distance(query_vec))
        .limit(CANDIDATE_DEPTH)
    )
    result = await session.execute(stmt)
    return [str(row) for row in result.scalars().all()]


async def keyword_arm(session: AsyncSession, course_id, query: str) -> list[str]:
    """Return up to `CANDIDATE_DEPTH` chunk ids matching `query` via
    `websearch_to_tsquery`, ranked by `ts_rank_cd`, scoped to `course_id`'s
    books only."""
    book_ids = select(Book.id).where(Book.course_id == course_id)
    tsquery = func.websearch_to_tsquery("english", query)
    stmt = (
        select(Chunk.id)
        .where(Chunk.book_id.in_(book_ids), Chunk.content_tsv.op("@@")(tsquery))
        .order_by(func.ts_rank_cd(Chunk.content_tsv, tsquery).desc())
        .limit(CANDIDATE_DEPTH)
    )
    result = await session.execute(stmt)
    return [str(row) for row in result.scalars().all()]


async def fetch_display(session: AsyncSession, fused_ids: list[str], query: str) -> list[Row]:
    """Fetch display fields + a `ts_headline` snippet for exactly the fused
    chunk ids, re-sorted into `fused_ids`' RRF-score order.

    Rows also carry the full chunk `text` and its `token_count`: search
    shows only `snippet`, chat grounds on `text` within a token budget."""
    if not fused_ids:
        return []
    tsquery = func.websearch_to_tsquery("english", query)
    stmt = (
        select(
            Chunk.id,
            Chunk.page_start,
            Chunk.page_end,
            Chunk.section_path,
            Book.id.label("book_id"),
            Book.original_filename,
            Chunk.text.label("text"),
            Chunk.token_count.label("token_count"),
            func.ts_headline("english", Chunk.text, tsquery, HEADLINE_OPTIONS).label("snippet"),
        )
        .join(Book, Book.id == Chunk.book_id)
        .where(Chunk.id.in_(fused_ids))
    )
    rows = (await session.execute(stmt)).all()
    by_id = {str(row.id): row for row in rows}
    return [by_id[chunk_id] for chunk_id in fused_ids if chunk_id in by_id]
