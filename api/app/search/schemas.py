"""Pydantic request/response schemas for the hybrid search endpoint.

`SearchRequest.q` rejects a blank/whitespace-only query with a 422 before
the retrieval query ever runs (defense in depth alongside the
client's disabled-submit-button guard) - `websearch_to_tsquery` never
raises on malformed input, so without this guard a blank query would fail
silently rather than loudly.
"""

import uuid

from pydantic import BaseModel, ConfigDict, Field, field_validator


class SearchRequest(BaseModel):
    q: str = Field(min_length=1)

    @field_validator("q")
    @classmethod
    def strip_and_reject_blank(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("Query must not be empty or whitespace-only.")
        return stripped


class SearchResult(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    book_id: uuid.UUID
    original_filename: str
    section_path: list[str]
    page_start: int
    page_end: int
    snippet: str
