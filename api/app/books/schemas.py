"""Pydantic response schema for book routes.

Deliberately does not expose `storage_path` - the internal filesystem path
is never returned to a client; PDFs are served only through an
authenticated, enrollment-checked route, never a public file
location (copyright/security constraint, PROJECT.md).
"""

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict

from app.books.models import BookStatus


class BookResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    course_id: uuid.UUID
    uploaded_by: uuid.UUID
    original_filename: str
    size_bytes: int
    status: BookStatus
    status_error: str | None
    created_at: datetime
