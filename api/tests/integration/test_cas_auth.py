"""Integration tests for SFU CAS sign-in against the fake CAS server.

Covers: the login redirect targets cas.sfu.ca with this app's callback as the
service; a valid ticket is validated SERVER-SIDE (the fake CAS records the
serviceValidate hit), creates a student user, and issues the same session
cookie as the magic-link path; a repeat sign-in reuses the row; a rejected
ticket yields 401, never a session.
"""

from urllib.parse import quote

import pytest
from httpx import AsyncClient
from sqlalchemy import select

from tests.conftest import FakeCasServer

pytestmark = pytest.mark.asyncio


async def test_cas_login_redirects_to_cas_with_callback_service(
    async_client: AsyncClient,
) -> None:
    from app.settings import get_settings

    settings = get_settings()

    response = await async_client.get("/auth/cas/login", follow_redirects=False)

    assert response.status_code == 302
    location = response.headers["location"]
    assert location.startswith(f"{settings.cas_base_url}/login?")
    assert quote(f"{settings.app_base_url}/api/auth/cas/callback", safe="") in location


async def test_valid_ticket_creates_student_and_issues_session(
    async_client: AsyncClient, fake_cas_server: FakeCasServer
) -> None:
    fake_cas_server.user = "jsmith"

    response = await async_client.get(
        "/auth/cas/callback", params={"ticket": "ST-123"}, follow_redirects=False
    )

    # Redirects into the app with the same session cookie the magic-link
    # path issues.
    assert response.status_code == 303, response.text
    assert "session" in response.cookies

    # The ticket became trusted only via a server-side serviceValidate call
    # to (the fake) CAS.
    assert len(fake_cas_server.requests) == 1
    validate_request = fake_cas_server.requests[0]
    assert validate_request.url.path.endswith("/serviceValidate")
    assert validate_request.url.params["ticket"] == "ST-123"
    assert validate_request.url.params["service"].endswith("/api/auth/cas/callback")

    # First CAS sign-in created the user: student by default,
    # auth_method cas, computing id captured.
    from app.db.base import async_session_factory
    from app.users.models import User

    async with async_session_factory() as session:
        result = await session.execute(select(User).where(User.email == "jsmith@sfu.ca"))
        user = result.scalar_one()
        assert user.role.value == "student"
        assert user.auth_method.value == "cas"
        assert user.computing_id == "jsmith"

    # The issued cookie is a working session: /auth/me resolves the user
    # (the client persisted the callback's Set-Cookie automatically).
    me = await async_client.get("/auth/me")
    assert me.status_code == 200
    body = me.json()
    assert body["email"] == "jsmith@sfu.ca"
    assert body["role"] == "student"
    assert body["auth_method"] == "cas"


async def test_repeat_cas_sign_in_reuses_existing_user(
    async_client: AsyncClient, fake_cas_server: FakeCasServer
) -> None:
    fake_cas_server.user = "repeat_cas"

    first = await async_client.get(
        "/auth/cas/callback", params={"ticket": "ST-1"}, follow_redirects=False
    )
    second = await async_client.get(
        "/auth/cas/callback", params={"ticket": "ST-2"}, follow_redirects=False
    )
    assert first.status_code == 303
    assert second.status_code == 303

    from app.db.base import async_session_factory
    from app.users.models import User

    async with async_session_factory() as session:
        result = await session.execute(select(User).where(User.email == "repeat_cas@sfu.ca"))
        assert len(result.scalars().all()) == 1


async def test_rejected_ticket_returns_401_without_session(
    async_client: AsyncClient, fake_cas_server: FakeCasServer
) -> None:
    fake_cas_server.mode = "failure"

    response = await async_client.get(
        "/auth/cas/callback", params={"ticket": "ST-forged"}, follow_redirects=False
    )

    assert response.status_code == 401
    assert "session" not in response.cookies


async def test_missing_ticket_returns_401(async_client: AsyncClient) -> None:
    response = await async_client.get("/auth/cas/callback", follow_redirects=False)

    assert response.status_code == 401
    assert "session" not in response.cookies
