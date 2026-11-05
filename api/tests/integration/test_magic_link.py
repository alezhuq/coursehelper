"""Integration tests for the magic-link round trip against a real Postgres
container.

Covers: request -> verify issues a session and creates a user row; a second
verify of the same token fails (single-use); a non-@sfu.ca
request never sends a link and returns 400.
"""

import re

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

pytestmark = pytest.mark.asyncio


async def _requested_link(async_client: AsyncClient, email: str) -> str:
    """Request a magic link and pull the verify URL out of Mailpit's inbox."""
    response = await async_client.post("/auth/magic-link/request", json={"email": email})
    assert response.status_code == 202, response.text

    from app.auth import magic_link as magic_link_module

    return magic_link_module.LAST_SENT_LINK  # test-only capture, see email.py


async def test_magic_link_round_trip_creates_session_and_user(async_client: AsyncClient) -> None:
    email = "student@sfu.ca"
    link = await _requested_link(async_client, email)
    # The emailed link lands on the frontend verify page (never the API):
    # redemption requires the explicit POST below, so mail scanners that
    # prefetch GET links cannot burn the single-use token.
    assert "/auth/verify?token=" in link
    token = re.search(r"token=([^&]+)", link).group(1)

    response = await async_client.post("/auth/magic-link/verify", json={"token": token})

    assert response.status_code == 200, response.text
    assert "session" in response.cookies

    from app.db.base import async_session_factory
    from app.users.models import User

    async with async_session_factory() as session:
        result = await session.execute(select(User).where(User.email == email))
        user = result.scalar_one()
        assert user.auth_method.value == "magic_link"


async def test_single_use_token_cannot_be_replayed(async_client: AsyncClient) -> None:
    email = "replay@sfu.ca"
    link = await _requested_link(async_client, email)
    token = re.search(r"token=([^&]+)", link).group(1)

    first = await async_client.post("/auth/magic-link/verify", json={"token": token})
    assert first.status_code == 200

    second = await async_client.post("/auth/magic-link/verify", json={"token": token})
    assert second.status_code in (400, 401)
    assert "session" not in second.cookies


async def test_mixed_case_email_normalizes_to_one_lowercase_account(
    async_client: AsyncClient,
) -> None:
    link = await _requested_link(async_client, "MiXeD.Case@sfu.ca")
    token = re.search(r"token=([^&]+)", link).group(1)

    response = await async_client.post("/auth/magic-link/verify", json={"token": token})
    assert response.status_code == 200

    from app.db.base import async_session_factory
    from app.users.models import User

    async with async_session_factory() as session:
        result = await session.execute(select(User).where(User.email == "mixed.case@sfu.ca"))
        assert result.scalar_one_or_none() is not None


async def test_repeat_request_for_same_email_is_rate_limited(async_client: AsyncClient) -> None:
    email = "throttle@sfu.ca"

    first = await async_client.post("/auth/magic-link/request", json={"email": email})
    assert first.status_code == 202, first.text

    second = await async_client.post("/auth/magic-link/request", json={"email": email})
    assert second.status_code == 429


async def test_verify_from_browser_without_login_state_is_rejected_without_burning_token(
    async_client: AsyncClient,
) -> None:
    email = "otherbrowser@sfu.ca"
    link = await _requested_link(async_client, email)
    token = re.search(r"token=([^&]+)", link).group(1)

    # A different client (mail scanner, attacker-delivered link) has no
    # login_state cookie and must be rejected (login-CSRF binding).
    from app.main import app

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as other_browser:
        rejected = await other_browser.post("/auth/magic-link/verify", json={"token": token})
    assert rejected.status_code == 400
    assert "session" not in rejected.cookies

    # Rejection happens BEFORE redemption, so the browser that actually
    # requested the link can still sign in with it.
    accepted = await async_client.post("/auth/magic-link/verify", json={"token": token})
    assert accepted.status_code == 200, accepted.text


async def test_non_sfu_domain_request_returns_400_and_creates_no_row(
    async_client: AsyncClient,
) -> None:
    response = await async_client.post(
        "/auth/magic-link/request", json={"email": "student@gmail.com"}
    )

    assert response.status_code == 400

    from app.db.base import async_session_factory
    from app.users.models import MagicLinkRequest

    async with async_session_factory() as session:
        result = await session.execute(
            select(MagicLinkRequest).where(MagicLinkRequest.email == "student@gmail.com")
        )
        assert result.scalar_one_or_none() is None
