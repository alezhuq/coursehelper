"""Repository interface the authorization dependencies resolve against.

Kept deliberately tiny and injectable: the dependencies call the module-level
`enrollments_repo`/`courses_repo` instances, so a test (or a future caller)
can substitute a fake by monkeypatching those names without touching the DB.
The contract test suite (tests/unit/test_authz.py) exercises the real
implementations against real stub tables instead, per the project's
testcontainers testing philosophy.
"""

import uuid

from sqlalchemy import exists as sa_exists
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.courses.models import Course
from app.enrollments.models import Enrollment


class EnrollmentsRepo:
    async def exists(self, session: AsyncSession, user_id: uuid.UUID, course_id: uuid.UUID) -> bool:
        """True iff (user_id, course_id) has an enrollments row."""
        result = await session.execute(
            select(
                sa_exists().where(Enrollment.user_id == user_id, Enrollment.course_id == course_id)
            )
        )
        return bool(result.scalar())


class CoursesRepo:
    async def get(self, session: AsyncSession, course_id: uuid.UUID) -> Course | None:
        result = await session.execute(select(Course).where(Course.id == course_id))
        return result.scalar_one_or_none()


enrollments_repo = EnrollmentsRepo()
courses_repo = CoursesRepo()
