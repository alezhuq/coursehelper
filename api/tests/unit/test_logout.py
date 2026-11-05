"""Logout is strictly local.

POST /auth/logout must clear only this app's session cookie and must never
make an outbound HTTP request - in particular never to any CAS logout
endpoint, which would force-log the user out of every other CAS-integrated
SFU service they have open. Asserted by recording every httpx send during
the logout call: the only request observed is the test's own.
"""

import httpx
import pytest
from httpx import AsyncClient

pytestmark = pytest.mark.asyncio


async def test_logout_clears_cookie_and_makes_no_cas_request(
    async_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    sent_urls: list[str] = []
    original_send = httpx.AsyncClient.send

    async def recording_send(self, request, *args, **kwargs):  # type: ignore[no-untyped-def]
        sent_urls.append(str(request.url))
        return await original_send(self, request, *args, **kwargs)

    monkeypatch.setattr(httpx.AsyncClient, "send", recording_send)

    response = await async_client.post("/auth/logout")

    assert response.status_code == 200
    assert response.json() == {"status": "logged_out"}

    # The session cookie is deleted (Max-Age=0 / expired Set-Cookie).
    set_cookie = response.headers["set-cookie"]
    assert set_cookie.startswith('session="";') or set_cookie.startswith("session=;")
    assert "Max-Age=0" in set_cookie or "max-age=0" in set_cookie.lower()

    # The ONLY request that flowed through httpx is the test's own call to
    # the app under test - no outbound request to CAS (or anywhere else).
    assert sent_urls == ["http://test/auth/logout"]

    from app.settings import get_settings

    cas_base = get_settings().cas_base_url
    assert all(cas_base not in url for url in sent_urls)
    assert all("cas" not in url for url in sent_urls)
