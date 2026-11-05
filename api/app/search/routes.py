"""Hybrid search route: POST /courses/{course_id}/search.

Read-only, so this only depends on `require_course_enrollment` - no
`require_custom_header` (CSRF), matching `list_books`'s convention in
`api/app/books/routes.py` that GET/read routes skip the CSRF header while
mutating routes require it. The route stays thin: embed the query, run both
retrieval arms, fuse, fetch display rows, map to `SearchResult` - any
`HTTPException` (403 from the enrollment dependency, 422 from
`SearchRequest` validation) is raised by its owning layer, not here.
"""

import asyncio
import uuid

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz.dependencies import require_course_enrollment
from app.db.base import get_session
from app.ingestion.embedding import embed_batch
from app.search import repo
from app.search.rrf import fuse_rrf
from app.search.schemas import SearchRequest, SearchResult
from app.users.models import User

router = APIRouter(prefix="/courses/{course_id}/search", tags=["search"])


@router.post("", response_model=list[SearchResult])
async def search_course(
    course_id: uuid.UUID,
    body: SearchRequest,
    _user: User = Depends(require_course_enrollment),
    session: AsyncSession = Depends(get_session),
) -> list[SearchResult]:
    query_vec = (await asyncio.to_thread(embed_batch, [body.q]))[0]

    vector_ids = await repo.vector_arm(session, course_id, query_vec)
    keyword_ids = await repo.keyword_arm(session, course_id, body.q)
    fused_ids = fuse_rrf(vector_ids, keyword_ids)

    rows = await repo.fetch_display(session, fused_ids, body.q)
    return [SearchResult.model_validate(row) for row in rows]
