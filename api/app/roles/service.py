"""Role-request service: submit, list-pending, approve, deny.

Every mutation goes through here rather than routes touching the ORM
directly, so "one pending request per user" and "approve promotes to
professor / deny leaves the role unchanged" live in exactly one place.
Authorization (who may call approve/deny/list) is enforced entirely by the
route-level `require_role("admin")` dependency - this module never
re-derives or duplicates that check, and never looks at request ownership
(T-01-19: approve/deny admits any admin, regardless of whose request it is).
"""

import uuid
from datetime import UTC, datetime

from fastapi import HTTPException
from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.roles.models import RoleRequest, RoleRequestStatus
from app.users.models import Role, User


async def create_role_request(session: AsyncSession, user: User) -> RoleRequest:
    """Create a pending role request, or return the user's existing one.

    Idempotent and race-safe to one active (pending) request per user: the
    partial unique index backs this at the DB layer, and the insert uses
    `ON CONFLICT DO NOTHING` so a concurrent submission returns the single
    pending row instead of raising an `IntegrityError`.
    """
    pending_stmt = select(RoleRequest).where(
        RoleRequest.user_id == user.id,
        RoleRequest.status == RoleRequestStatus.pending,
    )
    pending = (await session.execute(pending_stmt)).scalar_one_or_none()
    if pending is not None:
        return pending

    # index_where is a literal predicate (not a bound parameter): Postgres
    # must prove the partial-index predicate to infer the arbiter index.
    await session.execute(
        pg_insert(RoleRequest)
        .values(user_id=user.id)
        .on_conflict_do_nothing(index_elements=["user_id"], index_where=text("status = 'pending'"))
    )
    return (await session.execute(pending_stmt)).scalar_one()


async def list_pending_requests(session: AsyncSession) -> list[RoleRequest]:
    result = await session.execute(
        select(RoleRequest)
        .where(RoleRequest.status == RoleRequestStatus.pending)
        .order_by(RoleRequest.requested_at)
    )
    return list(result.scalars().all())


async def get_latest_request_for_user(session: AsyncSession, user: User) -> RoleRequest | None:
    """Return the signed-in user's most recent role request, or `None`.

    Read-only, side-effect-free lookup for the dashboard's "current request
    status" display (none/pending/approved/denied) - unlike
    `create_role_request`, this never creates a row, so simply loading the
    dashboard can never submit a request on the user's behalf.
    """
    result = await session.execute(
        select(RoleRequest)
        .where(RoleRequest.user_id == user.id)
        .order_by(RoleRequest.requested_at.desc())
        .limit(1)
    )
    return result.scalar_one_or_none()


async def _get_pending_or_error(session: AsyncSession, request_id: uuid.UUID) -> RoleRequest:
    result = await session.execute(select(RoleRequest).where(RoleRequest.id == request_id))
    request = result.scalar_one_or_none()
    if request is None:
        raise HTTPException(status_code=404, detail="Role request not found")
    if request.status != RoleRequestStatus.pending:
        raise HTTPException(status_code=409, detail="Role request already decided")
    return request


async def approve_role_request(
    session: AsyncSession, request_id: uuid.UUID, admin: User
) -> RoleRequest:
    """Approve a pending request: promote the requester to professor."""
    request = await _get_pending_or_error(session, request_id)
    target = await session.get(User, request.user_id)
    if target is None:
        raise HTTPException(status_code=404, detail="Requesting user no longer exists")

    target.role = Role.professor
    request.status = RoleRequestStatus.approved
    request.decided_at = datetime.now(UTC)
    request.decided_by = admin.id
    await session.flush()
    return request


async def deny_role_request(
    session: AsyncSession, request_id: uuid.UUID, admin: User
) -> RoleRequest:
    """Deny a pending request: record the decision, role stays unchanged."""
    request = await _get_pending_or_error(session, request_id)

    request.status = RoleRequestStatus.denied
    request.decided_at = datetime.now(UTC)
    request.decided_by = admin.id
    await session.flush()
    return request
