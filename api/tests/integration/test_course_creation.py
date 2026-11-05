"""Integration tests for course creation: a professor can create a
course with code/title/term, the owner is auto-enrolled in the same
transaction (without this the owning professor loses search/chat
access to their own course), and a non-professor is rejected with 403.

Calls `app/courses/repo.py` directly (mirrors test_role_requests.py's
direct-service-call shape) and drives the HTTP layer for the role-gate
check (mirrors test_role_request_status_route.py's async_client shape).
"""

from httpx import AsyncClient
from sqlalchemy import select
from testcontainers.postgres import PostgresContainer

from app.courses.repo import create_course
from app.courses.schemas import CourseCreate
from app.db.base import async_session_factory
from app.enrollments.models import Enrollment
from app.users.models import Role, User
from app.users.repo import upsert_user


async def _make_user(session, email: str, role: Role = Role.student) -> User:
    user = await upsert_user(
        session,
        email=email,
        computing_id=email.split("@")[0],
        auth_method="cas",
        display_name=email.split("@")[0],
    )
    user.role = role
    await session.flush()
    return user


async def test_professor_creates_course(postgres_container: PostgresContainer) -> None:
    async with async_session_factory() as session:
        professor = await _make_user(session, "crs-prof-1@sfu.ca", Role.professor)
        payload = CourseCreate(
            code="CMPT 120", title="Introduction to Computing Science", term="Fall 2026"
        )

        course = await create_course(session, owner=professor, payload=payload)
        await session.commit()

        assert course.code == "CMPT 120"
        assert course.title == "Introduction to Computing Science"
        assert course.term == "Fall 2026"
        assert course.owner_id == professor.id


async def test_owner_auto_enrolled(postgres_container: PostgresContainer) -> None:
    async with async_session_factory() as session:
        professor = await _make_user(session, "crs-prof-2@sfu.ca", Role.professor)
        payload = CourseCreate(code="CMPT 225", title="Data Structures", term="Fall 2026")

        course = await create_course(session, owner=professor, payload=payload)
        await session.commit()

        result = await session.execute(
            select(Enrollment).where(
                Enrollment.user_id == professor.id, Enrollment.course_id == course.id
            )
        )
        rows = result.scalars().all()
        assert len(rows) == 1


async def test_non_professor_cannot_create(async_client: AsyncClient) -> None:
    login_response = await async_client.post(
        "/test-auth/login", json={"computing_id": "crs-student-1", "role": "student"}
    )
    assert login_response.status_code == 200

    response = await async_client.post(
        "/courses",
        json={
            "code": "CMPT 120",
            "title": "Introduction to Computing Science",
            "term": "Fall 2026",
        },
        headers={"X-Requested-With": "fetch"},
    )

    assert response.status_code == 403
