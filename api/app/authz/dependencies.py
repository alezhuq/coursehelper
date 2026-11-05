"""Centralized authorization dependencies.

Every protected route in every later phase composes one of these three
dependencies instead of hand-rolling its own check:

- ``require_role(*roles)``: gates by role alone (course/upload
  creation). Callers list every admitted role explicitly, including
  ``"admin"`` - there is no implicit bypass here, so a deliberate
  professor-only route stays professor-only.
- ``require_course_enrollment``: gates course content access - search, chat,
  PDF pages. Admins bypass; everyone else needs an enrollments
  row for exactly this course.
- ``require_course_owner_or_admin``: gates course mutation - upload, delete,
  retry. Admins bypass; everyone else must own the course.

STRUCTURAL CONSTRAINT: every dependency here resolves
access strictly from ``(user, course_id)``. No function in this module
accepts, or may ever accept, a bare ``book_id`` or ``chunk_id`` - a route
that holds a book or chunk identifier must first re-derive its owning
``course_id`` and pass that. This makes the cross-course access bypass (a
route trusting a client-supplied content id and skipping the enrollment
check) structurally impossible to write through this module, and the
contract test suite enforces the shape.

Role and ownership facts are always read live from the DB: the user row
comes from ``get_current_user`` (which re-fetches per request, never trusting
cookie claims) and enrollment/ownership go through ``repos`` - so an admin's
revocation takes effect on the very next request.
"""

import uuid
from collections.abc import Awaitable, Callable

from fastapi import Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.session import get_current_user
from app.authz import repos
from app.db.base import get_session
from app.users.models import Role, User


def require_role(*roles: str | Role) -> Callable[..., Awaitable[User]]:
    """Return a dependency admitting only users whose role is in ``roles``.

    Callers include every admitted role explicitly (e.g.
    ``require_role("professor", "admin")``); admin is not implicitly added.
    Resolves from the live user row only - no content identifiers involved.
    """
    allowed = {Role(role) for role in roles}

    async def _check(user: User = Depends(get_current_user)) -> User:
        if user.role not in allowed:
            raise HTTPException(
                status_code=403,
                detail=f"Requires one of roles: {sorted(r.value for r in allowed)}",
            )
        return user

    return _check


async def require_course_enrollment(
    course_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db_session: AsyncSession = Depends(get_session),
) -> User:
    """Admit the user iff they are an admin or enrolled in ``course_id``.

    Access is resolved strictly from ``(user, course_id)``; this dependency
    never accepts a bare ``book_id``/``chunk_id`` - callers holding a content
    identifier must re-derive its owning ``course_id`` first.
    """
    if user.role == Role.admin:
        return user
    if await repos.enrollments_repo.exists(db_session, user.id, course_id):
        return user
    raise HTTPException(status_code=403, detail="Not enrolled in this course")


async def require_course_owner_or_admin(
    course_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db_session: AsyncSession = Depends(get_session),
) -> User:
    """Admit the user iff they are an admin or own the course ``course_id``.

    A ``course_id`` that resolves to no course denies (403), never errors
    open. Access is resolved strictly from ``(user, course_id)``; this
    dependency never accepts a bare ``book_id``/``chunk_id``.
    """
    if user.role == Role.admin:
        return user
    course = await repos.courses_repo.get(db_session, course_id)
    if course is None or course.owner_id != user.id:
        raise HTTPException(status_code=403, detail="Not the course owner")
    return user
