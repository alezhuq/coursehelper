"""Course persistence: create (with owner auto-enroll), list, get, join.

`create_course` inserts the owner's `Enrollment` row in the same
transaction as the `Course` row -
without this the owning professor would fail
`require_course_enrollment` on their own course for search and chat, since that
dependency never bypasses for ownership, only for admin.

`join_course` is idempotent and race-safe: a repeated or concurrent join for
the same `(user_id, course_id)` pair never inserts a duplicate row and never
raises (`INSERT ... ON CONFLICT DO NOTHING` on `uq_enrollments_user_course`),
matching the "a double-click or revisit is a success, not an error"
contract. Follows this codebase's convention (`api/app/roles/service.py`)
of raising `HTTPException` from the repo/service layer, keeping routes thin.
"""

import uuid

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.courses.models import Course
from app.courses.schemas import CourseCreate
from app.enrollments.models import Enrollment
from app.users.models import User


async def create_course(session: AsyncSession, *, owner: User, payload: CourseCreate) -> Course:
    course = Course(owner_id=owner.id, code=payload.code, title=payload.title, term=payload.term)
    session.add(course)
    await session.flush()  # course.id now populated

    session.add(Enrollment(user_id=owner.id, course_id=course.id))
    return course


async def list_courses(session: AsyncSession) -> list[Course]:
    result = await session.execute(select(Course).order_by(Course.created_at.desc()))
    return list(result.scalars().all())


async def get_course(session: AsyncSession, course_id: uuid.UUID) -> Course:
    result = await session.execute(select(Course).where(Course.id == course_id))
    course = result.scalar_one_or_none()
    if course is None:
        raise HTTPException(status_code=404, detail="Course not found")
    return course


async def join_course(session: AsyncSession, *, user: User, course_id: uuid.UUID) -> None:
    await get_course(session, course_id)  # 404 if the course doesn't exist

    existing = await session.execute(
        select(Enrollment).where(Enrollment.user_id == user.id, Enrollment.course_id == course_id)
    )
    if existing.scalar_one_or_none() is not None:
        return  # idempotent - already enrolled is success, not an error

    # Two concurrent joins can both pass the select above; ON CONFLICT DO
    # NOTHING makes the loser a silent no-op instead of an IntegrityError 500.
    await session.execute(
        pg_insert(Enrollment)
        .values(user_id=user.id, course_id=course_id)
        .on_conflict_do_nothing(constraint="uq_enrollments_user_course")
    )
