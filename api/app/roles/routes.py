"""Role-request routes: submit, admin list, admin approve/deny.

The admin list and approve/deny are gated by `require_role("admin")`;
all three mutating routes additionally require the custom
CSRF header (`app/auth/csrf.py`). A student calling approve/deny on
their own request id still gets 403 - authorization here never inspects
request ownership, only the caller's role.
"""

import uuid

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.csrf import require_custom_header
from app.auth.session import get_current_user
from app.authz.dependencies import require_role
from app.db.base import get_session
from app.roles import service
from app.roles.models import RoleRequest
from app.roles.schemas import RoleRequestResponse
from app.users.models import User

router = APIRouter(prefix="/auth/role-requests", tags=["roles"])


@router.post("", response_model=RoleRequestResponse, dependencies=[Depends(require_custom_header)])
async def submit_role_request(
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> RoleRequest:
    request = await service.create_role_request(session, user)
    await session.commit()
    return request


@router.get("", response_model=list[RoleRequestResponse])
async def list_role_requests(
    _admin: User = Depends(require_role("admin")),
    session: AsyncSession = Depends(get_session),
) -> list[RoleRequest]:
    return await service.list_pending_requests(session)


@router.get("/me", response_model=RoleRequestResponse | None)
async def my_role_request(
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> RoleRequest | None:
    """The signed-in user's own most recent request (or `null`), for the
    dashboard's status display. Read-only - unlike `POST /auth/role-requests`
    this never creates a row, so loading the dashboard is never itself a
    submission."""
    return await service.get_latest_request_for_user(session, user)


@router.post(
    "/{request_id}/approve",
    response_model=RoleRequestResponse,
    dependencies=[Depends(require_custom_header)],
)
async def approve_role_request_route(
    request_id: uuid.UUID,
    admin: User = Depends(require_role("admin")),
    session: AsyncSession = Depends(get_session),
) -> RoleRequest:
    request = await service.approve_role_request(session, request_id, admin)
    await session.commit()
    return request


@router.post(
    "/{request_id}/deny",
    response_model=RoleRequestResponse,
    dependencies=[Depends(require_custom_header)],
)
async def deny_role_request_route(
    request_id: uuid.UUID,
    admin: User = Depends(require_role("admin")),
    session: AsyncSession = Depends(get_session),
) -> RoleRequest:
    request = await service.deny_role_request(session, request_id, admin)
    await session.commit()
    return request
