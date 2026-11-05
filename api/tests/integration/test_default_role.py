"""Integration test: a brand-new user defaults to the student role,
regardless of which auth path created the row.
"""

import pytest
from httpx import AsyncClient
from sqlalchemy import select

pytestmark = pytest.mark.asyncio


async def test_new_user_defaults_to_student(async_client: AsyncClient) -> None:
    from app.db.base import async_session_factory
    from app.users.repo import upsert_user

    async with async_session_factory() as session:
        user = await upsert_user(
            session,
            email="newuser@sfu.ca",
            computing_id=None,
            auth_method="magic_link",
            display_name="newuser",
        )
        await session.commit()

        assert user.role.value == "student"

        from app.users.models import User

        result = await session.execute(select(User).where(User.id == user.id))
        fetched = result.scalar_one()
        assert fetched.role.value == "student"
