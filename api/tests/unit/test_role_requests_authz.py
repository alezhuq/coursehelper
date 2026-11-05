"""Contract tests: a non-admin cannot approve/deny/list role requests.

Runs against the real Postgres testcontainers stack via the HTTP layer so
`require_role("admin")` is exercised as the routes will
actually enforce it, not re-derived by hand.
"""

from httpx import AsyncClient

CSRF_HEADERS = {"X-Requested-With": "fetch"}


async def _login(client: AsyncClient, computing_id: str, role: str) -> None:
    response = await client.post(
        "/test-auth/login", json={"computing_id": computing_id, "role": role}
    )
    assert response.status_code == 200


async def test_non_admin_cannot_list_pending_requests(async_client: AsyncClient) -> None:
    await _login(async_client, "authz-student-1", "student")

    response = await async_client.get("/auth/role-requests")

    assert response.status_code == 403


async def test_non_admin_cannot_approve(async_client: AsyncClient) -> None:
    await _login(async_client, "authz-student-2", "student")
    submitted = await async_client.post("/auth/role-requests", headers=CSRF_HEADERS)
    request_id = submitted.json()["id"]

    response = await async_client.post(
        f"/auth/role-requests/{request_id}/approve", headers=CSRF_HEADERS
    )

    assert response.status_code == 403


async def test_non_admin_cannot_deny(async_client: AsyncClient) -> None:
    await _login(async_client, "authz-student-3", "student")
    submitted = await async_client.post("/auth/role-requests", headers=CSRF_HEADERS)
    request_id = submitted.json()["id"]

    response = await async_client.post(
        f"/auth/role-requests/{request_id}/deny", headers=CSRF_HEADERS
    )

    assert response.status_code == 403


async def test_student_cannot_self_approve_their_own_request(async_client: AsyncClient) -> None:
    """Approve is gated by the caller's role alone, never by
    request ownership - a student approving their own request id still
    gets 403, and their own role never changes as a side effect."""
    await _login(async_client, "authz-student-4", "student")
    submitted = await async_client.post("/auth/role-requests", headers=CSRF_HEADERS)
    request_id = submitted.json()["id"]

    response = await async_client.post(
        f"/auth/role-requests/{request_id}/approve", headers=CSRF_HEADERS
    )

    assert response.status_code == 403

    me = await async_client.get("/auth/me")
    assert me.json()["role"] == "student"
