"""Integration tests for the admin-bootstrap mechanism.

`maybe_bootstrap_admin` is invoked inside `users_repo.upsert_user` - the
single join point both the CAS callback and magic-link verify routes call -
so promotion is exercised here via `upsert_user` directly, matching the "on
first sign-in" scope. The guard must promote only while zero admins exist
and must never re-grant admin once any admin exists, even if the env var
stays set.
"""

import pytest
from sqlalchemy import select, update
from testcontainers.postgres import PostgresContainer

from app.db.base import async_session_factory
from app.settings import get_settings
from app.users.models import Role, User
from app.users.repo import upsert_user


@pytest.fixture
def bootstrap_email(monkeypatch: pytest.MonkeyPatch):
    def _set(email: str | None) -> None:
        if email is None:
            monkeypatch.delenv("BOOTSTRAP_ADMIN_EMAIL", raising=False)
        else:
            monkeypatch.setenv("BOOTSTRAP_ADMIN_EMAIL", email)
        get_settings.cache_clear()

    yield _set
    monkeypatch.delenv("BOOTSTRAP_ADMIN_EMAIL", raising=False)
    get_settings.cache_clear()


async def _demote_all_admins() -> None:
    """Force the "zero admins exist" precondition explicitly.

    The Postgres container is shared (session-scoped) across the whole test
    run, and other test modules (e.g. `test_authz.py`,
    `test_role_requests.py`) legitimately seed their own admin users. A
    "while zero admins exist" test must not depend on file/test execution
    order to happen to see an empty table, so it arranges that precondition
    directly rather than assuming it.
    """
    async with async_session_factory() as session:
        await session.execute(update(User).where(User.role == Role.admin).values(role=Role.student))
        await session.commit()


async def test_first_sign_in_matching_email_is_promoted_while_zero_admins(
    bootstrap_email, postgres_container: PostgresContainer
) -> None:
    await _demote_all_admins()
    bootstrap_email("bootstrap1@sfu.ca")

    async with async_session_factory() as session:
        user = await upsert_user(
            session,
            email="bootstrap1@sfu.ca",
            computing_id="bootstrap1",
            auth_method="cas",
            display_name="bootstrap1",
        )
        await session.commit()

    assert user.role == Role.admin


async def test_maybe_bootstrap_admin_is_directly_callable_and_idempotent(
    bootstrap_email, postgres_container: PostgresContainer
) -> None:
    from app.users.repo import maybe_bootstrap_admin

    await _demote_all_admins()
    bootstrap_email("direct-bootstrap@sfu.ca")

    async with async_session_factory() as session:
        user = await upsert_user(
            session,
            email="direct-bootstrap@sfu.ca",
            computing_id="direct-bootstrap",
            auth_method="cas",
            display_name="direct-bootstrap",
        )
        # upsert_user already invoked the bootstrap check; calling it again
        # directly must be a harmless no-op (idempotent), pinning the
        # symbol's own contract.
        await maybe_bootstrap_admin(session, user)
        await session.commit()

    assert user.role == Role.admin


async def test_non_matching_email_is_not_promoted(
    bootstrap_email, postgres_container: PostgresContainer
) -> None:
    bootstrap_email("bootstrap2@sfu.ca")

    async with async_session_factory() as session:
        user = await upsert_user(
            session,
            email="someone-else@sfu.ca",
            computing_id="someone-else",
            auth_method="cas",
            display_name="someone-else",
        )
        await session.commit()

    assert user.role == Role.student


async def test_matching_email_not_promoted_once_an_admin_already_exists(
    bootstrap_email, postgres_container: PostgresContainer
) -> None:
    async with async_session_factory() as session:
        existing_admin = await upsert_user(
            session,
            email="already-admin@sfu.ca",
            computing_id="already-admin",
            auth_method="cas",
            display_name="already-admin",
        )
        existing_admin.role = Role.admin
        await session.commit()

    bootstrap_email("bootstrap3@sfu.ca")

    async with async_session_factory() as session:
        user = await upsert_user(
            session,
            email="bootstrap3@sfu.ca",
            computing_id="bootstrap3",
            auth_method="cas",
            display_name="bootstrap3",
        )
        await session.commit()

    assert user.role == Role.student


async def test_re_sign_in_does_not_re_promote_once_another_admin_exists(
    bootstrap_email, postgres_container: PostgresContainer
) -> None:
    await _demote_all_admins()
    bootstrap_email("bootstrap4@sfu.ca")

    async with async_session_factory() as session:
        user = await upsert_user(
            session,
            email="bootstrap4@sfu.ca",
            computing_id="bootstrap4",
            auth_method="cas",
            display_name="bootstrap4",
        )
        await session.commit()
    assert user.role == Role.admin

    # Demote the bootstrap user back to student, then seed a different
    # admin - count(admins) is > 0 again, so the still-set env var must not
    # re-promote the bootstrap user on its next sign-in.
    async with async_session_factory() as session:
        result = await session.execute(select(User).where(User.email == "bootstrap4@sfu.ca"))
        fetched = result.scalar_one()
        fetched.role = Role.student
        await session.commit()

    async with async_session_factory() as session:
        other_admin = await upsert_user(
            session,
            email="other-admin-bootstrap4@sfu.ca",
            computing_id="other-admin-bootstrap4",
            auth_method="cas",
            display_name="other-admin",
        )
        other_admin.role = Role.admin
        await session.commit()

    async with async_session_factory() as session:
        signed_in_again = await upsert_user(
            session,
            email="bootstrap4@sfu.ca",
            computing_id="bootstrap4",
            auth_method="cas",
            display_name="bootstrap4",
        )
        await session.commit()

    assert signed_in_again.role == Role.student
