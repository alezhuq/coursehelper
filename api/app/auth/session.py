"""Signed HttpOnly session cookie.

The cookie value carries only the user id, never role or email - role must
always be re-fetched from the DB via `get_current_user` so an admin's live
role change (e.g. professor approval) takes effect on the very
next request rather than waiting for the cookie to expire and reissue.
"""

import uuid

from fastapi import Cookie, Depends, HTTPException, Response
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.base import get_session
from app.settings import get_settings
from app.users import repo as users_repo
from app.users.models import User

SESSION_COOKIE_NAME = "session"
SESSION_MAX_AGE = 60 * 60 * 24 * 30  # 30 days - "stay signed in"


def _serializer() -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(get_settings().session_secret, salt="session")


def issue_session_cookie(user_id: uuid.UUID | str) -> str:
    return _serializer().dumps({"uid": str(user_id)})


def read_session_cookie(token: str) -> str | None:
    try:
        data = _serializer().loads(token, max_age=SESSION_MAX_AGE)
    except (BadSignature, SignatureExpired):
        return None
    uid = data.get("uid")
    return uid if isinstance(uid, str) else None


def set_session_cookie(response: Response, user_id: uuid.UUID | str) -> None:
    settings = get_settings()
    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=issue_session_cookie(user_id),
        max_age=SESSION_MAX_AGE,
        httponly=True,
        secure=settings.environment != "development",
        samesite="lax",
    )


def clear_session_cookie(response: Response) -> None:
    response.delete_cookie(key=SESSION_COOKIE_NAME)


async def get_current_user(
    session_token: str | None = Cookie(default=None, alias=SESSION_COOKIE_NAME),
    db_session: AsyncSession = Depends(get_session),
) -> User:
    if session_token is None:
        raise HTTPException(status_code=401, detail="Not authenticated")

    user_id = read_session_cookie(session_token)
    if user_id is None:
        raise HTTPException(status_code=401, detail="Invalid or expired session")

    user = await users_repo.get(db_session, user_id)
    if user is None:
        raise HTTPException(status_code=401, detail="User no longer exists")

    return user
