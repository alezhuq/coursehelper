"""User persistence: upsert-by-email and id lookup.

`upsert_user` is the single place a `User` row is created across both auth
paths (CAS and magic-link) - new users always default to the
`student` role; an existing user's role is never overwritten by a
later sign-in (so an admin-granted professor role sticks), except by the
admin-bootstrap check below.

`maybe_bootstrap_admin` runs on every sign-in through `upsert_user` (new or
existing user) so the admin-bootstrap mechanism applies uniformly to both auth paths without either
`app/auth/routes.py` callback needing to know about it.
"""

import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.settings import get_settings
from app.users.models import AuthMethod, Role, User


async def maybe_bootstrap_admin(session: AsyncSession, user: User) -> None:
    """Promote `user` to admin iff `BOOTSTRAP_ADMIN_EMAIL` matches their
    email and zero admins currently exist.

    This guard is the entire mitigation for a standing env var
    re-granting admin later: once any admin exists - the bootstrap user
    included - this is a permanent no-op, even if the env var stays set.
    """
    bootstrap_email = get_settings().bootstrap_admin_email
    if not bootstrap_email:
        return
    if user.email.lower() != bootstrap_email.lower():
        return
    if user.role == Role.admin:
        return

    admin_count = await session.scalar(
        select(func.count()).select_from(User).where(User.role == Role.admin)
    )
    if admin_count == 0:
        user.role = Role.admin
        await session.flush()


async def upsert_user(
    session: AsyncSession,
    *,
    email: str,
    computing_id: str | None,
    auth_method: AuthMethod | str,
    display_name: str,
) -> User:
    result = await session.execute(select(User).where(User.email == email))
    existing = result.scalar_one_or_none()
    if existing is not None:
        await maybe_bootstrap_admin(session, existing)
        return existing

    user = User(
        email=email,
        computing_id=computing_id,
        display_name=display_name,
        role=Role.student,
        auth_method=AuthMethod(auth_method),
    )
    session.add(user)
    await session.flush()
    await maybe_bootstrap_admin(session, user)
    return user


async def get(session: AsyncSession, user_id: uuid.UUID | str) -> User | None:
    result = await session.execute(select(User).where(User.id == user_id))
    return result.scalar_one_or_none()
