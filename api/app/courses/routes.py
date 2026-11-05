"""Course routes: create, browse, self-join.

`POST /courses` is gated by `require_role("professor", "admin")` - a
student creating a course is denied 403. `GET /courses` and
`POST /{course_id}/join` only require `get_current_user`: any
authenticated user may browse and self-join a course in the pilot. Every mutating route
also requires the custom CSRF header (`app/auth/csrf.py`),
matching the app's same-origin CSRF model.
"""

import uuid

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.csrf import require_custom_header
from app.auth.session import get_current_user
from app.authz.dependencies import require_role
from app.courses import repo
from app.courses.models import Course
from app.courses.schemas import CourseCreate, CourseResponse
from app.db.base import get_session
from app.users.models import User

router = APIRouter(prefix="/courses", tags=["courses"])


@router.post("", response_model=CourseResponse, dependencies=[Depends(require_custom_header)])
async def create_course_route(
    payload: CourseCreate,
    user: User = Depends(require_role("professor", "admin")),
    session: AsyncSession = Depends(get_session),
) -> Course:
    course = await repo.create_course(session, owner=user, payload=payload)
    await session.commit()
    return course


@router.get("", response_model=list[CourseResponse])
async def list_courses_route(
    _user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> list[Course]:
    return await repo.list_courses(session)


@router.post(
    "/{course_id}/join",
    status_code=204,
    dependencies=[Depends(require_custom_header)],
)
async def join_course_route(
    course_id: uuid.UUID,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> None:
    await repo.join_course(session, user=user, course_id=course_id)
    await session.commit()
