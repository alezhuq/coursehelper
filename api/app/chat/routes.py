"""Chat routes: condense -> retrieve -> gate -> generate ->
stream -> cite -> persist, one route, no framework abstraction between the
steps.

Ownership/authz note: the conversation-ownership re-derivation
runs as its own FastAPI dependency (`_require_owned_conversation`), NOT as
an inline check inside the streaming generator. Once `EventSourceResponse`
starts producing its body, the 200 status and `text/event-stream` headers
are already committed on the wire - an `HTTPException` raised from inside
the generator body cannot cleanly become a 404 at that point. Dependencies
resolve (and can raise) before the SSE machinery is ever constructed, which
is what gives a foreign `conversation_id` a real 404 response instead of a
broken/truncated stream.
"""

import asyncio
import logging
import re
import uuid
from collections.abc import AsyncIterable

from fastapi import APIRouter, Depends, HTTPException
from fastapi.sse import EventSourceResponse, ServerSentEvent
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.csrf import require_custom_header
from app.authz.dependencies import require_course_enrollment
from app.chat import repo as chat_repo
from app.chat.citations import resolve_citations
from app.chat.gate import is_empty_retrieval

# Imported by name (not `from app.chat import llm_client` + attribute
# access) so tests can monkeypatch `app.chat.routes.condense_query` /
# `app.chat.routes.stream_generation` directly - the same convention
# `app/search/routes.py` uses for `embed_batch` (monkeypatched at its
# call-site import name, not inside app.ingestion.embedding).
from app.chat.llm_client import condense_query, stream_generation
from app.chat.models import Conversation
from app.chat.prompts import REFUSAL_MESSAGE, SYSTEM_PROMPT
from app.chat.schemas import ConversationResponse, MessageResponse, PostMessageRequest
from app.db.base import get_session
from app.ingestion.embedding import embed_batch
from app.search import repo as search_repo
from app.search.rrf import fuse_rrf
from app.settings import get_settings
from app.users.models import User

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/courses/{course_id}/chat", tags=["chat"])


def select_sources_within_budget(sources: list, budget_tokens: int) -> list:
    """Cap the retrieved chunks sent to the model at `budget_tokens`.

    Walks `sources` in RRF order, always keeps the top-ranked chunk (even if
    it alone exceeds the budget - answering from the best chunk beats
    answering from nothing), and stops at the FIRST row that would push the
    running `token_count` total over the budget: it never skips ahead to a
    smaller lower-ranked chunk and never truncates a chunk's text, so every
    source block the model sees is a whole chunk and ranking order is
    preserved. The returned list is the ONE list that must feed both
    `build_messages` and `resolve_citations`.
    """
    kept: list = []
    total = 0
    for source in sources:
        if kept and total + source.token_count > budget_tokens:
            break
        kept.append(source)
        total += source.token_count
    return kept


def build_messages(
    system_prompt: str, history: list[dict], sources: list, question: str
) -> list[dict]:
    """Assemble the OpenAI-shaped message list for the generation call.

    Retrieved chunk text (the FULL stored chunk `text`, not the search
    highlight snippet) is wrapped in delimited `<source>` blocks inside the
    USER role only, never concatenated into the system role - a
    planted instruction in professor-uploaded PDF text can therefore never
    escalate into something the model treats as authoritative. Literal
    `<source` / `</source` sequences inside chunk text are neutralized so
    untrusted PDF text cannot close its own block and forge another.
    """
    source_blocks = "\n".join(
        f'<source id="{i}">{_neutralize_source_tags(source.text)}</source>'
        for i, source in enumerate(sources, start=1)
    )
    user_content = f"{source_blocks}\n\nQuestion: {question}"
    return [
        {"role": "system", "content": system_prompt},
        *history,
        {"role": "user", "content": user_content},
    ]


_SOURCE_TAG_RE = re.compile(r"<(/?source)", re.IGNORECASE)


def _neutralize_source_tags(text: str) -> str:
    """Escape the `<` of any `<source` / `</source` sequence in chunk text
    so it cannot be read as a source-block delimiter."""
    return _SOURCE_TAG_RE.sub(r"&lt;\1", text)


async def _require_owned_conversation(
    course_id: uuid.UUID,
    conversation_id: uuid.UUID,
    user: User = Depends(require_course_enrollment),
    session: AsyncSession = Depends(get_session),
) -> Conversation:
    conversation = await chat_repo.get_owned_conversation(
        session, conversation_id, user.id, course_id
    )
    if conversation is None:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return conversation


@router.get("/conversation", response_model=ConversationResponse)
async def get_conversation(
    course_id: uuid.UUID,
    user: User = Depends(require_course_enrollment),
    session: AsyncSession = Depends(get_session),
) -> ConversationResponse:
    conversation = await chat_repo.get_or_create_conversation(session, user.id, course_id)
    await session.commit()
    messages = await chat_repo.load_all_messages(session, conversation.id)
    return ConversationResponse(
        conversation_id=conversation.id,
        messages=[MessageResponse.model_validate(m) for m in messages],
    )


@router.delete(
    "/conversation",
    status_code=204,
    dependencies=[Depends(require_custom_header)],
)
async def clear_conversation(
    course_id: uuid.UUID,
    user: User = Depends(require_course_enrollment),
    session: AsyncSession = Depends(get_session),
) -> None:
    """Deletes every persisted message for the caller's (user, course)
    conversation, keeping the `Conversation` row itself (see
    `chat_repo.delete_messages_for_conversation`'s docstring for why).

    Deliberately takes NO `conversation_id` path/body parameter - the
    target is resolved entirely from the authenticated (user, course) pair
    via `get_or_create_conversation`, so there is no foreign-id path to
    defend against here, and a student who has never chatted in this
    course gets a harmless 204 (creates the same empty conversation
    `get_conversation` would have, then deletes zero rows) rather than a
    404.
    """
    conversation = await chat_repo.get_or_create_conversation(session, user.id, course_id)
    await chat_repo.delete_messages_for_conversation(session, conversation.id)
    await session.commit()


@router.post(
    "/conversations/{conversation_id}/messages",
    response_class=EventSourceResponse,
    dependencies=[Depends(require_custom_header)],
)
async def post_message(
    course_id: uuid.UUID,
    body: PostMessageRequest,
    conversation: Conversation = Depends(_require_owned_conversation),
    session: AsyncSession = Depends(get_session),
) -> AsyncIterable[ServerSentEvent]:
    # 1. Persist the user turn immediately - it happened regardless of what
    #    follows. Try-again semantics: if the conversation's latest message
    #    is an unanswered user turn with identical text, this is the
    #    client's Try again after a failed turn. Reuse that row instead of
    #    inserting a second copy, and exclude it from history so the
    #    question neither appears twice in the prompt nor triggers a
    #    spurious condensation call on a first-turn retry. An identical
    #    question after an ANSWERED turn (latest is the assistant) is still
    #    a brand-new turn.
    latest = await chat_repo.get_latest_message(session, conversation.id)
    if latest is not None and latest.role == "user" and latest.content == body.content:
        history = await chat_repo.load_recent_turns(
            session, conversation.id, limit=3, exclude_message_id=latest.id
        )
    else:
        history = await chat_repo.load_recent_turns(session, conversation.id, limit=3)
        await chat_repo.insert_user_message(session, conversation.id, body.content)
    await session.commit()

    # 2. Condense (skip entirely on the conversation's first turn - it's
    #    already standalone).
    # 3. Retrieve - reuse the hybrid search exactly as-is.
    # Both steps share one broad except: an unhandled httpx/network/shape
    # error from condense_query (anything besides the narrow LLMCondenseError
    # JSON-retry exhaustion) must never escape as an unhandled exception mid
    # SSE-stream - it has to surface as the same graceful `error` event the
    # generation step (below) already guarantees.
    try:
        standalone_query = (
            body.content if not history else await condense_query(history, body.content)
        )
        query_vec = (await asyncio.to_thread(embed_batch, [standalone_query]))[0]
        vector_ids = await search_repo.vector_arm(session, course_id, query_vec)
        keyword_ids = await search_repo.keyword_arm(session, course_id, standalone_query)
        fused_ids = fuse_rrf(vector_ids, keyword_ids)
    except Exception:
        logger.exception(
            "Query condensation or retrieval failed for conversation %s", conversation.id
        )
        await session.rollback()
        yield ServerSentEvent(
            data="Something went wrong preparing your question. Please try again.",
            event="error",
        )
        return

    # 4. Gate - zero retrieved chunks means NO LLM call and no invented
    #    answer.
    if is_empty_retrieval(fused_ids):
        await chat_repo.insert_assistant_message(
            session, conversation.id, REFUSAL_MESSAGE, citations=[]
        )
        await session.commit()
        yield ServerSentEvent(data=REFUSAL_MESSAGE, event="token")
        yield ServerSentEvent(event="done")
        return

    fetched = await search_repo.fetch_display(session, fused_ids, standalone_query)
    # The ONE budget-truncated list feeds both the prompt and the citation
    # resolver: if they diverged, a `[n]` marker could point at a chunk the
    # model never saw (citation-trust guarantee).
    sources = select_sources_within_budget(fetched, get_settings().llm_source_token_budget)
    prompt_messages = build_messages(SYSTEM_PROMPT, history, sources, standalone_query)

    # End the retrieval transaction (including the transaction-scoped
    # `SET LOCAL hnsw.iterative_scan` from vector_arm) and return the pooled
    # connection BEFORE the LLM stream: a slow generation must not hold a
    # DB connection "idle in transaction" for its whole duration. The
    # assistant insert below opens a fresh transaction.
    # `expire_on_commit=False` keeps `conversation` usable and `sources`
    # are plain Row objects, so nothing needs reloading.
    await session.commit()

    # 5. Generate + stream - forward tokens to the browser as they arrive;
    #    accumulate the full text for citation validation + persistence. A
    #    mid-stream failure must NEVER persist a partial assistant turn
    #    (concurrency guarantee) - the user turn already committed
    #    above is left standing so the student can simply re-ask.
    full_answer = ""
    try:
        async for delta in stream_generation(prompt_messages):
            full_answer += delta
            yield ServerSentEvent(data=delta, event="token")
    except Exception:
        logger.exception("Generation stream interrupted for conversation %s", conversation.id)
        yield ServerSentEvent(
            data="The answer could not be completed. Please try again.", event="error"
        )
        return

    citations = resolve_citations(full_answer, sources)
    await chat_repo.insert_assistant_message(session, conversation.id, full_answer, citations)
    await session.commit()
    yield ServerSentEvent(
        data=[span.model_dump(mode="json") for span in citations], event="citations"
    )
    yield ServerSentEvent(event="done")
