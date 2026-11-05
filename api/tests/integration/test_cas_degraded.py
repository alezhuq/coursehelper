"""Integration tests for CAS degraded mode.

An unreachable / timing-out / 5xx-ing CAS must yield a DISTINCT 503 with the
`cas_unavailable` code - never a hang and never a generic 500 - and a browser
navigation must be bounced back to the login page with `?cas=unavailable` so
the always-visible magic-link fallback is surfaced.
"""

import pytest
from httpx import AsyncClient

from tests.conftest import FakeCasServer

pytestmark = pytest.mark.asyncio


async def test_cas_timeout_returns_distinct_503(
    async_client: AsyncClient, fake_cas_server: FakeCasServer
) -> None:
    fake_cas_server.mode = "timeout"

    response = await async_client.get(
        "/auth/cas/callback", params={"ticket": "ST-timeout"}, follow_redirects=False
    )

    assert response.status_code == 503, response.text
    detail = response.json()["detail"]
    assert detail["code"] == "cas_unavailable"
    assert "magic-link" in detail["message"]
    assert "session" not in response.cookies


async def test_cas_5xx_returns_distinct_503(
    async_client: AsyncClient, fake_cas_server: FakeCasServer
) -> None:
    fake_cas_server.mode = "error"

    response = await async_client.get(
        "/auth/cas/callback", params={"ticket": "ST-error"}, follow_redirects=False
    )

    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "cas_unavailable"
    assert "session" not in response.cookies


async def test_browser_navigation_redirects_to_login_fallback(
    async_client: AsyncClient, fake_cas_server: FakeCasServer
) -> None:
    """A real browser (Accept: text/html) lands back on the login page with
    ?cas=unavailable instead of a raw JSON error, so the UI can point at the
    magic-link fallback (degraded-mode UX)."""
    fake_cas_server.mode = "timeout"

    response = await async_client.get(
        "/auth/cas/callback",
        params={"ticket": "ST-timeout"},
        headers={"accept": "text/html,application/xhtml+xml"},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"].endswith("/login?cas=unavailable")
    assert "session" not in response.cookies
