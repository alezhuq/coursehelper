"""Contract tests for the centralized authorization dependencies.

These tests are the documented contract every later phase relies on:

- ``require_role(*roles)`` 403s a user whose role is not in the allowed set
  and admits allowed roles (callers include ``"admin"`` explicitly).
- ``require_course_enrollment`` 403s a non-enrolled non-admin, admits an
  enrolled user, and admits any admin (admin bypass).
- ``require_course_owner_or_admin`` 403s a non-owner non-admin, admits the
  course owner, and admits any admin (admin bypass).
- Every check resolves access strictly from ``(user, course_id)``: no
  dependency in the module accepts a bare book/chunk identifier, and an
  unrelated ``course_id`` never grants access.

They run against real stub ``courses``/``enrollments`` tables in the
testcontainers Postgres (migrated by ``conftest.postgres_container``), not
mocks, so FK/unique behavior is exercised for real.
"""

import inspect
import uuid
from dataclasses import dataclass

import pytest
import pytest_asyncio
from fastapi import HTTPException
from testcontainers.postgres import PostgresContainer

from app.authz import dependencies as authz_dependencies
from app.authz.dependencies import (
    require_course_enrollment,
    require_course_owner_or_admin,
    require_role,
)

# No module-level asyncio pytestmark: asyncio_mode = "auto" (pyproject.toml)
# already marks the async tests, and a blanket mark would warn on the two
# sync signature-shape tests at the bottom of this file.


@dataclass
class Seed:
    """Seeded actors and courses for the authorization contract."""

    student: object  # not enrolled anywhere
    enrolled_student: object  # enrolled in course_id only
    owner_professor: object  # owns course_id
    other_professor: object  # owns nothing
    admin: object
    course_id: uuid.UUID
    other_course_id: uuid.UUID  # no enrollments, owned by other_professor


@pytest_asyncio.fixture
async def seed(postgres_container: PostgresContainer) -> Seed:
    from app.courses.models import Course
    from app.db.base import async_session_factory
    from app.enrollments.models import Enrollment
    from app.users.models import Role
    from app.users.repo import upsert_user

    async with async_session_factory() as session:

        async def make_user(email: str, role: Role) -> object:
            user = await upsert_user(
                session,
                email=email,
                computing_id=None,
                auth_method="magic_link",
                display_name=email.split("@")[0],
            )
            user.role = role
            await session.flush()
            return user

        student = await make_user("authz.student@sfu.ca", Role.student)
        enrolled_student = await make_user("authz.enrolled@sfu.ca", Role.student)
        owner_professor = await make_user("authz.owner@sfu.ca", Role.professor)
        other_professor = await make_user("authz.otherprof@sfu.ca", Role.professor)
        admin = await make_user("authz.admin@sfu.ca", Role.admin)

        course = Course(
            owner_id=owner_professor.id,
            code="CMPT 300",
            title="Operating Systems",
            term="Fall 2026",
        )
        other_course = Course(
            owner_id=other_professor.id, code="CMPT 354", title="Databases", term="Fall 2026"
        )
        session.add_all([course, other_course])
        await session.flush()

        session.add(Enrollment(user_id=enrolled_student.id, course_id=course.id))
        await session.commit()

        return Seed(
            student=student,
            enrolled_student=enrolled_student,
            owner_professor=owner_professor,
            other_professor=other_professor,
            admin=admin,
            course_id=course.id,
            other_course_id=other_course.id,
        )


async def _expect_403(coro) -> None:
    with pytest.raises(HTTPException) as exc_info:
        await coro
    assert exc_info.value.status_code == 403


# --- require_role -----------------------------------------------------------


async def test_require_role_admits_professor(seed: Seed) -> None:
    check = require_role("professor", "admin")
    assert await check(user=seed.owner_professor) is seed.owner_professor


async def test_require_role_admits_admin(seed: Seed) -> None:
    check = require_role("professor", "admin")
    assert await check(user=seed.admin) is seed.admin


async def test_require_role_403s_student(seed: Seed) -> None:
    check = require_role("professor", "admin")
    await _expect_403(check(user=seed.student))


# --- require_course_enrollment ----------------------------------------------


async def test_enrollment_403s_non_enrolled_student(seed: Seed) -> None:
    from app.db.base import async_session_factory

    async with async_session_factory() as session:
        await _expect_403(
            require_course_enrollment(seed.course_id, user=seed.student, db_session=session)
        )


async def test_enrollment_admits_enrolled_student(seed: Seed) -> None:
    from app.db.base import async_session_factory

    async with async_session_factory() as session:
        user = await require_course_enrollment(
            seed.course_id, user=seed.enrolled_student, db_session=session
        )
        assert user is seed.enrolled_student


async def test_enrollment_admits_admin_without_enrollment_row(seed: Seed) -> None:
    from app.db.base import async_session_factory

    async with async_session_factory() as session:
        user = await require_course_enrollment(seed.course_id, user=seed.admin, db_session=session)
        assert user is seed.admin


async def test_enrollment_in_one_course_grants_nothing_in_another(seed: Seed) -> None:
    """An unrelated course_id never grants access: enrollment is per-course."""
    from app.db.base import async_session_factory

    async with async_session_factory() as session:
        await _expect_403(
            require_course_enrollment(
                seed.other_course_id, user=seed.enrolled_student, db_session=session
            )
        )


# --- require_course_owner_or_admin ------------------------------------------


async def test_ownership_403s_non_owner_professor(seed: Seed) -> None:
    from app.db.base import async_session_factory

    async with async_session_factory() as session:
        await _expect_403(
            require_course_owner_or_admin(
                seed.course_id, user=seed.other_professor, db_session=session
            )
        )


async def test_ownership_admits_owner(seed: Seed) -> None:
    from app.db.base import async_session_factory

    async with async_session_factory() as session:
        user = await require_course_owner_or_admin(
            seed.course_id, user=seed.owner_professor, db_session=session
        )
        assert user is seed.owner_professor


async def test_ownership_admits_admin(seed: Seed) -> None:
    from app.db.base import async_session_factory

    async with async_session_factory() as session:
        user = await require_course_owner_or_admin(
            seed.course_id, user=seed.admin, db_session=session
        )
        assert user is seed.admin


async def test_ownership_403s_for_nonexistent_course(seed: Seed) -> None:
    """A course_id that resolves to no course must deny, not error open."""
    from app.db.base import async_session_factory

    async with async_session_factory() as session:
        await _expect_403(
            require_course_owner_or_admin(
                uuid.uuid4(), user=seed.owner_professor, db_session=session
            )
        )


# --- (user, course_id) resolution shape --------------------


def test_dependencies_take_course_id_and_compose_get_current_user() -> None:
    for dep in (require_course_enrollment, require_course_owner_or_admin):
        params = inspect.signature(dep).parameters
        assert "course_id" in params, f"{dep.__name__} must take course_id explicitly"
        assert "user" in params, f"{dep.__name__} must compose get_current_user via Depends"


def test_no_code_path_resolves_access_from_bare_book_or_chunk_id() -> None:
    """No function in the authz dependency module may accept a bare book_id or
    chunk_id: access is always derived from (user, course_id). This is the
    structural guard against the cross-course access bypass."""
    forbidden = {"book_id", "chunk_id"}
    for name, obj in vars(authz_dependencies).items():
        if callable(obj) and getattr(obj, "__module__", None) == authz_dependencies.__name__:
            params = set(inspect.signature(obj).parameters)
            assert not (params & forbidden), (
                f"{name} accepts {params & forbidden}; authz must resolve from (user, course_id)"
            )
