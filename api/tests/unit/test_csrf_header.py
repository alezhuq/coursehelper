"""Contract tests for the custom-header CSRF guard on mutating role routes.

The first test exercises `app.auth.csrf.require_custom_header` directly;
the remaining tests exercise the full contract through the HTTP layer.
"""

from httpx import AsyncClient
from starlette.requests import Request

CSRF_HEADERS = {"X-Requested-With": "fetch"}


async def test_require_custom_header_rejects_a_request_missing_the_header() -> None:
    from fastapi import HTTPException

    from app.auth.csrf import require_custom_header

    scope = {"type": "http", "headers": [], "method": "POST"}
    request = Request(scope)

    raised = False
    try:
        await require_custom_header(request)
    except HTTPException as exc:
        raised = True
        assert exc.status_code == 403
    assert raised, "expected HTTPException for a missing CSRF header"


async def test_submit_without_csrf_header_is_rejected(async_client: AsyncClient) -> None:
    await _login(async_client, "csrf-student-1", "student")

    response = await async_client.post("/auth/role-requests")

    assert response.status_code == 403


async def test_approve_without_csrf_header_is_rejected(async_client: AsyncClient) -> None:
    await _login(async_client, "csrf-student-2", "student")
    submitted = await async_client.post("/auth/role-requests", headers=CSRF_HEADERS)
    request_id = submitted.json()["id"]

    await _login(async_client, "csrf-admin-1", "admin")
    response = await async_client.post(f"/auth/role-requests/{request_id}/approve")

    assert response.status_code == 403


async def test_deny_without_csrf_header_is_rejected(async_client: AsyncClient) -> None:
    await _login(async_client, "csrf-student-3", "student")
    submitted = await async_client.post("/auth/role-requests", headers=CSRF_HEADERS)
    request_id = submitted.json()["id"]

    await _login(async_client, "csrf-admin-2", "admin")
    response = await async_client.post(f"/auth/role-requests/{request_id}/deny")

    assert response.status_code == 403


async def test_submit_with_csrf_header_is_accepted(async_client: AsyncClient) -> None:
    await _login(async_client, "csrf-student-4", "student")

    response = await async_client.post("/auth/role-requests", headers=CSRF_HEADERS)

    assert response.status_code == 200


async def _login(client: AsyncClient, computing_id: str, role: str) -> None:
    response = await client.post(
        "/test-auth/login", json={"computing_id": computing_id, "role": role}
    )
    assert response.status_code == 200
