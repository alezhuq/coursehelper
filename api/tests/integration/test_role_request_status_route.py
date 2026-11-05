"""Integration tests for `GET /auth/role-requests/me`.

The dashboard needs a side-effect-free way to show the signed-in user's own request
status (none/pending/approved/denied) without the read itself creating a
request, which the idempotent `POST /auth/role-requests` would otherwise do
if reused for this purpose.
"""

from httpx import AsyncClient


async def _login(client: AsyncClient, computing_id: str, role: str) -> None:
    response = await client.post(
        "/test-auth/login", json={"computing_id": computing_id, "role": role}
    )
    assert response.status_code == 200


async def test_my_role_request_is_null_when_none_submitted(async_client: AsyncClient) -> None:
    await _login(async_client, "me-route-student-1", "student")

    response = await async_client.get("/auth/role-requests/me")

    assert response.status_code == 200
    assert response.json() is None


async def test_my_role_request_reflects_pending_after_submission(async_client: AsyncClient) -> None:
    await _login(async_client, "me-route-student-2", "student")
    await async_client.post("/auth/role-requests", headers={"X-Requested-With": "fetch"})

    response = await async_client.get("/auth/role-requests/me")

    assert response.status_code == 200
    assert response.json()["status"] == "pending"


async def test_my_role_request_reflects_approved_after_admin_decision(
    async_client: AsyncClient,
) -> None:
    await _login(async_client, "me-route-student-3", "student")
    submitted = await async_client.post(
        "/auth/role-requests", headers={"X-Requested-With": "fetch"}
    )
    request_id = submitted.json()["id"]

    await _login(async_client, "me-route-admin-1", "admin")
    await async_client.post(
        f"/auth/role-requests/{request_id}/approve", headers={"X-Requested-With": "fetch"}
    )

    await _login(async_client, "me-route-student-3", "student")
    response = await async_client.get("/auth/role-requests/me")

    assert response.status_code == 200
    assert response.json()["status"] == "approved"


async def test_getting_my_role_request_never_creates_one(async_client: AsyncClient) -> None:
    await _login(async_client, "me-route-student-4", "student")

    await async_client.get("/auth/role-requests/me")
    still_none = await async_client.get("/auth/role-requests/me")

    assert still_none.json() is None
