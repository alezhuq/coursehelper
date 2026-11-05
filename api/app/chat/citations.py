"""`[n]` citation marker resolution.

Regex-parses `[n]` markers out of the already-streamed, already-final
answer text and looks each `n` up against the `sources` list built at
generation time: the budget-truncated list of hybrid search's
`search_repo.fetch_display` rows actually sent to the model (RRF-fused
order - source 1 is `sources[0]`, etc.). It must be the very same list
`build_messages` rendered, never the pre-truncation list, or a `[n]` marker
would point at a chunk the model never saw. No retry loop here: any `n`
outside `range(1, len(sources) + 1)` (the model citing a source index that
was never provided) is dropped and logged at WARNING, not surfaced to the
student as a broken citation link - re-generating the whole streamed
answer just to fix one bad marker would double the latency the student
already waited through.
"""

import logging
import re
from collections.abc import Sequence
from typing import Any

from app.chat.schemas import CitationSpan

logger = logging.getLogger(__name__)

_MARKER_RE = re.compile(r"\[(\d+)\]")


def resolve_citations(answer_text: str, sources: Sequence[Any]) -> list[CitationSpan]:
    """Build the denormalized `CitationSpan` list for every valid `[n]`
    marker in `answer_text`, de-duplicated and in first-seen order."""
    spans: list[CitationSpan] = []
    seen: set[int] = set()
    for match in _MARKER_RE.finditer(answer_text):
        n = int(match.group(1))
        if n in seen:
            continue
        if n < 1 or n > len(sources):
            logger.warning("model hallucinated citation index %d", n)
            continue
        seen.add(n)
        source = sources[n - 1]
        spans.append(
            CitationSpan(
                marker=n,
                book_id=source.book_id,
                original_filename=source.original_filename,
                page_start=source.page_start,
                page_end=source.page_end,
                section_path=list(source.section_path),
            )
        )
    return spans
