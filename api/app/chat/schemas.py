"""Pydantic request/response schemas for the chat endpoints.

`PostMessageRequest.content` rejects a blank/whitespace-only message with a
422 before the pipeline ever runs - mirrors `app/search/schemas.py::
SearchRequest`'s `strip_and_reject_blank` validator (defense in depth
alongside any client-side disabled-submit-button guard).
"""

import uuid

from pydantic import BaseModel, ConfigDict, Field, field_validator


class PostMessageRequest(BaseModel):
    content: str = Field(min_length=1)

    @field_validator("content")
    @classmethod
    def strip_and_reject_blank(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("Message content must not be empty or whitespace-only.")
        return stripped


class CondensedQuery(BaseModel):
    """Validates the query-condensation call's JSON-mode response before it
    is ever used to drive retrieval - a malformed/missing field here would
    otherwise poison retrieval silently."""

    standalone_query: str


class CitationSpan(BaseModel):
    """One resolved `[n]` marker, built AFTER the answer has fully streamed
    - denormalized so a citation still resolves to the right book/page even
    after that book's chunks are later re-ingested."""

    marker: int
    book_id: uuid.UUID
    original_filename: str
    page_start: int
    page_end: int
    section_path: list[str]


class MessageResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    role: str
    content: str
    citations: list[CitationSpan]


class ConversationResponse(BaseModel):
    conversation_id: uuid.UUID
    messages: list[MessageResponse]
