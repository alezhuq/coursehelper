"""Integration tests for the role-request service: submitting is
idempotent to one pending request, an admin's approval promotes the
requester to professor, and an admin's denial records the decision without
changing the role.

Calls `app/roles/service.py` functions directly against the real Postgres
testcontainers stack (a live `AsyncSession`, not mocks).
"""

import asyncio

from sqlalchemy import select
from testcontainers.postgres import PostgresContainer

from app.db.base import async_session_factory
from app.roles.models import RoleRequest, RoleRequestStatus
from app.roles.service import approve_role_request, create_role_request, deny_role_request
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


async def test_submit_creates_a_pending_request(postgres_container: PostgresContainer) -> None:
    async with async_session_factory() as session:
        student = await _make_user(session, "svc-student-1@sfu.ca")
        request = await create_role_request(session, student)
        await session.commit()

        assert request.status == RoleRequestStatus.pending
        assert request.user_id == student.id


async def test_submit_is_idempotent_to_one_pending_request(
    postgres_container: PostgresContainer,
) -> None:
    async with async_session_factory() as session:
        student = await _make_user(session, "svc-student-2@sfu.ca")
        first = await create_role_request(session, student)
        second = await create_role_request(session, student)
        await session.commit()

        assert first.id == second.id


async def test_approve_promotes_requester_and_records_decision(
    postgres_container: PostgresContainer,
) -> None:
    async with async_session_factory() as session:
        student = await _make_user(session, "svc-student-3@sfu.ca")
        admin = await _make_user(session, "svc-admin-1@sfu.ca", Role.admin)
        request = await create_role_request(session, student)
        await session.flush()

        approved = await approve_role_request(session, request.id, admin)
        await session.commit()

        assert approved.status == RoleRequestStatus.approved
        assert approved.decided_by == admin.id
        assert approved.decided_at is not None
        assert student.role == Role.professor


async def test_deny_records_decision_without_changing_role(
    postgres_container: PostgresContainer,
) -> None:
    async with async_session_factory() as session:
        student = await _make_user(session, "svc-student-4@sfu.ca")
        admin = await _make_user(session, "svc-admin-2@sfu.ca", Role.admin)
        request = await create_role_request(session, student)
        await session.flush()

        denied = await deny_role_request(session, request.id, admin)
        await session.commit()

        assert denied.status == RoleRequestStatus.denied
        assert denied.decided_by == admin.id
        assert denied.decided_at is not None
        assert student.role == Role.student


async def test_concurrent_submissions_share_one_pending_request(
    postgres_container: PostgresContainer,
) -> None:
    async with async_session_factory() as setup:
        student = await _make_user(setup, "svc-race-student@sfu.ca")
        await setup.commit()

    async with async_session_factory() as session_a, async_session_factory() as session_b:
        request_a = await create_role_request(session_a, student)
        task_b = asyncio.create_task(create_role_request(session_b, student))
        try:
            await wait_for_lock_wait(task_b)
        finally:
            await session_a.commit()
        request_b = await task_b  # must not raise IntegrityError

    assert request_b.id == request_a.id

    async with async_session_factory() as session:
        pending = (
            (
                await session.execute(
                    select(RoleRequest).where(
                        RoleRequest.user_id == student.id,
                        RoleRequest.status == RoleRequestStatus.pending,
                    )
                )
            )
            .scalars()
            .all()
        )
    assert len(pending) == 1
