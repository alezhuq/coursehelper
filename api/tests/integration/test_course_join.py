"""Integration tests for course browse + self-join: any
authenticated user can list all courses, joining is idempotent (a repeated
join is a success, not an error, since a double-click or revisit to an
already-joined course is an expected UI event), and joining a nonexistent
course is rejected.

Drives the HTTP layer via `async_client` + `/test-auth/login` (mirrors
test_role_request_status_route.py's shape) for route-level behavior, and
`app/courses/repo.py` directly for seeding courses.
"""

import asyncio
import uuid

from httpx import AsyncClient
from sqlalchemy import select

from app.courses.repo import create_course, join_course
from app.courses.schemas import CourseCreate
from app.db.base import async_session_factory
from app.enrollments.models import Enrollment
from app.users.models import Role, User
from app.users.repo import upsert_user
from tests.race import wait_for_lock_wait


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


async def _login(client: AsyncClient, computing_id: str, role: str) -> None:
    response = await client.post(
        "/test-auth/login", json={"computing_id": computing_id, "role": role}
    )
    assert response.status_code == 200


async def test_browse_lists_all_courses(async_client: AsyncClient) -> None:
    async with async_session_factory() as session:
        professor = await _make_user(session, "crs-join-prof-1@sfu.ca", Role.professor)
        course_a = await create_course(
            session,
            owner=professor,
            payload=CourseCreate(
                code="CMPT 120", title="Introduction to Computing Science", term="Fall 2026"
            ),
        )
        course_b = await create_course(
            session,
            owner=professor,
            payload=CourseCreate(code="CMPT 225", title="Data Structures", term="Fall 2026"),
        )
        await session.commit()
        course_ids = {str(course_a.id), str(course_b.id)}

    await _login(async_client, "crs-join-student-1", "student")

    response = await async_client.get("/courses")

    assert response.status_code == 200
    listed_ids = {course["id"] for course in response.json()}
    assert course_ids <= listed_ids


async def test_join_is_idempotent(async_client: AsyncClient) -> None:
    async with async_session_factory() as session:
        professor = await _make_user(session, "crs-join-prof-2@sfu.ca", Role.professor)
        course = await create_course(
            session,
            owner=professor,
            payload=CourseCreate(code="CMPT 300", title="Operating Systems", term="Fall 2026"),
        )
        await session.commit()
        course_id = course.id

    await _login(async_client, "crs-join-student-2", "student")

    first = await async_client.post(
        f"/courses/{course_id}/join", headers={"X-Requested-With": "fetch"}
    )
    second = await async_client.post(
        f"/courses/{course_id}/join", headers={"X-Requested-With": "fetch"}
    )

    assert first.status_code == 204
    assert second.status_code == 204

    async with async_session_factory() as session:
        student = await _make_user(session, "crs-join-student-2@sfu.ca", Role.student)
        result = await session.execute(
            select(Enrollment).where(
                Enrollment.user_id == student.id, Enrollment.course_id == course_id
            )
        )
        rows = result.scalars().all()
        assert len(rows) == 1


async def test_join_nonexistent_course_denied(async_client: AsyncClient) -> None:
    await _login(async_client, "crs-join-student-3", "student")

    response = await async_client.post(
        f"/courses/{uuid.uuid4()}/join", headers={"X-Requested-With": "fetch"}
    )

    assert response.status_code == 404


async def test_concurrent_joins_never_raise_and_leave_one_enrollment(
    async_client: AsyncClient,
) -> None:
    async with async_session_factory() as setup:
        professor = await _make_user(setup, "crs-join-race-prof@sfu.ca", Role.professor)
        course = await create_course(
            setup,
            owner=professor,
            payload=CourseCreate(
                code="CMPT 310", title="Artificial Intelligence", term="Fall 2026"
            ),
        )
        student = await _make_user(setup, "crs-join-race-student@sfu.ca", Role.student)
        await setup.commit()
        course_id = course.id

    async def _join_and_commit(session) -> None:
        await join_course(session, user=student, course_id=course_id)
        await session.commit()

    async with async_session_factory() as session_a, async_session_factory() as session_b:
        await join_course(session_a, user=student, course_id=course_id)
        await session_a.flush()
        task_b = asyncio.create_task(_join_and_commit(session_b))
        try:
            await wait_for_lock_wait(task_b)
        finally:
            await session_a.commit()
        await task_b  # must not raise IntegrityError

    async with async_session_factory() as session:
        rows = (
            (
                await session.execute(
                    select(Enrollment).where(
                        Enrollment.user_id == student.id, Enrollment.course_id == course_id
                    )
                )
            )
            .scalars()
            .all()
        )
    assert len(rows) == 1
