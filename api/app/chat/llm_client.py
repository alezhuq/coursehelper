"""The only module that talks to the env-configured OpenAI-compatible LLM
endpoint (provider swap is a `.env` change only, never a code
change - `base_url`/`model` are read exclusively from `get_settings()`).

Two entry points, deliberately different shapes: `condense_query` awaits a full
non-streaming JSON-mode
response (retrieval can't start until the complete standalone-query string
exists, so there is no partial consumer for condensation tokens);
`stream_generation` yields token deltas as they arrive (perceived latency
during a multi-second generation is the entire point of the "streaming
answers" requirement).
"""

import json
import logging
from collections.abc import AsyncIterable

import httpx
from pydantic import ValidationError

from app.chat.prompts import CONDENSE_SYSTEM_PROMPT
from app.chat.schemas import CondensedQuery
from app.settings import get_settings

logger = logging.getLogger(__name__)

_CONDENSE_MAX_ATTEMPTS = 3  # 1 initial + 2 corrective retries


class LLMCondenseError(Exception):
    """Raised when 3 consecutive condensation attempts fail to produce
    valid JSON. Never caught to silently fall back to the raw,
    un-condensed question - that failure mode is invisible to the student
    (retrieval would just quietly return worse results with no error)."""


class LLMGenerationError(Exception):
    """Raised when the streaming generation call fails or is interrupted
    mid-stream (upstream 5xx, connection drop, etc.)."""


async def condense_query(history: list[dict], question: str) -> str:
    """Rewrite a follow-up question into a standalone retrieval query.

    Non-streaming, temperature 0.0, max_tokens 120: a deterministic rewrite
    of an existing question, not a creative task, and a standalone question
    is always short.
    """
    settings = get_settings()
    payload: dict = {
        "model": settings.llm_condense_model or settings.llm_model,
        "temperature": 0.0,
        "max_tokens": 120,
        "response_format": {"type": "json_object"},
        "messages": [
            {"role": "system", "content": CONDENSE_SYSTEM_PROMPT},
            *history[-6:],  # last 3 (user, assistant) turn pairs, verbatim
            {
                "role": "user",
                "content": (
                    f'Rewrite as a standalone question: "{question}"\n'
                    'Return JSON: {"standalone_query": "..."}'
                ),
            },
        ],
    }
    async with httpx.AsyncClient(
        base_url=settings.llm_base_url,
        timeout=httpx.Timeout(connect=5.0, read=30.0, write=10.0, pool=5.0),
    ) as client:
        for attempt in range(_CONDENSE_MAX_ATTEMPTS):
            resp = await client.post("/chat/completions", json=payload)
            resp.raise_for_status()
            raw = resp.json()["choices"][0]["message"]["content"]
            try:
                return CondensedQuery.model_validate_json(raw).standalone_query
            except (ValidationError, json.JSONDecodeError) as exc:
                logger.warning("condense_query invalid JSON (attempt %d): %s", attempt, exc)
                payload["messages"].append({"role": "assistant", "content": raw})
                payload["messages"].append(
                    {"role": "user", "content": "That was not valid JSON. Return only JSON."}
                )
        raise LLMCondenseError("Could not produce a valid standalone query.")


async def stream_generation(messages: list[dict]) -> AsyncIterable[str]:
    """Yield raw token-delta strings from the OpenAI-compatible streaming
    endpoint. Caller (`app/chat/routes.py`) re-wraps each delta as its own
    app-level SSE `token` event - never forwards the upstream `data: {...}`
    line verbatim to the browser (two independent SSE layers)."""
    settings = get_settings()
    payload = {
        "model": settings.llm_model,
        "temperature": 0.2,
        "max_tokens": 800,
        "stream": True,
        "messages": messages,
    }
    async with httpx.AsyncClient(
        base_url=settings.llm_base_url,
        # read=None ONLY here: token-by-token generation can legitimately
        # pause between chunks for seconds on CPU-only Ollama; a finite
        # read timeout would abort a slow-but-healthy stream mid-answer.
        timeout=httpx.Timeout(connect=5.0, read=None, write=10.0, pool=5.0),
    ) as client:
        async with client.stream("POST", "/chat/completions", json=payload) as response:
            response.raise_for_status()
            async for line in response.aiter_lines():  # NOT .iter_lines() (sync)
                if not line.startswith("data: "):
                    continue
                data = line[len("data: ") :]
                if data == "[DONE]":
                    break
                delta = json.loads(data)["choices"][0]["delta"].get("content")
                if delta:
                    yield delta
