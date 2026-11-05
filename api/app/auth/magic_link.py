"""Magic-link token issue/verify with single-use enforcement.

`itsdangerous` alone proves a token's integrity and recency (`max_age`) but
not that it hasn't already been redeemed, so every issued token is paired
with a `MagicLinkRequest` row; `verify_magic_link` rejects a bad signature,
an expired token, a missing row, a mismatched email, a login-state value
that does not match the requesting browser's cookie (login-CSRF binding),
or a `used_at` that is already set.
"""

import secrets
from datetime import UTC, datetime, timedelta

from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.email import send_magic_link_email
from app.settings import get_settings
from app.users.models import MagicLinkRequest

MAGIC_LINK_MAX_AGE = 15 * 60  # 15 minutes

# Login-CSRF guard: the route sets this cookie when a link is
# requested and embeds the same random value in the token payload, binding
# flow initiation to completion. Verification rejects a token whose state
# does not match the requesting browser's cookie, so an attacker cannot
# sign a victim's browser into an attacker-controlled account by handing
# it a magic link the victim never requested.
LOGIN_STATE_COOKIE_NAME = "login_state"

# Abuse control (unauthenticated endpoint): at most one link per email per
# this interval, enforced against the persisted MagicLinkRequest rows so it
# holds across workers. nginx adds a per-IP limit_req on top of this.
MAGIC_LINK_RESEND_INTERVAL_SECONDS = 60

# Test-only capture of the last link built, so integration tests can drive
# the real verify flow without parsing Mailpit's inbox over SMTP/HTTP. Never
# read in production code paths.
LAST_SENT_LINK: str | None = None


class InvalidMagicLinkDomain(ValueError):
    """Raised when a magic-link request targets a non-@sfu.ca address."""


class MagicLinkRateLimited(Exception):
    """Raised when a link was already sent to this email very recently."""


def _serializer() -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(get_settings().session_secret, salt="magic-link")


def _is_sfu_email(email: str) -> bool:
    # Reject lookalike domains (e.g. "a@sfu.ca.evil.com", "a@notsfu.ca") by
    # requiring an exact, case-insensitive "@sfu.ca" suffix with nothing
    # else after it, not a substring match.
    return email.strip().lower().endswith("@sfu.ca") and email.strip() == email


async def request_magic_link(
    session: AsyncSession, email: str, login_state: str | None = None
) -> None:
    if not _is_sfu_email(email):
        raise InvalidMagicLinkDomain("Magic link is restricted to @sfu.ca addresses")

    # Normalize once at the boundary so the throttle lookup, the stored
    # MagicLinkRequest row, the token payload, and the eventual upsert_user
    # all agree on one canonical form. The CAS path always lowercases
    # (routes.py), so without this the same person signing in as
    # "JDoe@sfu.ca" here and "jdoe" via CAS would get two user rows.
    email = email.strip().lower()

    latest_request_at = await session.scalar(
        select(func.max(MagicLinkRequest.created_at)).where(MagicLinkRequest.email == email)
    )
    if latest_request_at is not None and datetime.now(UTC) - latest_request_at < timedelta(
        seconds=MAGIC_LINK_RESEND_INTERVAL_SECONDS
    ):
        raise MagicLinkRateLimited(
            "A sign-in link was sent to this address moments ago. "
            "Wait a minute before requesting another."
        )

    nonce = secrets.token_urlsafe(16)
    session.add(MagicLinkRequest(email=email, nonce=nonce))
    await session.flush()

    token = _serializer().dumps({"email": email, "nonce": nonce, "state": login_state})
    # The link lands on the frontend verify page (an SPA route served by
    # nginx's history fallback), NOT the API endpoint: redemption only
    # happens when that page's "Sign in" button POSTs the token to
    # /api/auth/magic-link/verify. Email link scanners (O365/Outlook
    # prefetch) follow GETs in inbound mail; a GET that redeemed the
    # single-use token would burn it before the user ever clicked.
    link = f"{get_settings().app_base_url}/auth/verify?token={token}"

    global LAST_SENT_LINK
    LAST_SENT_LINK = link

    await send_magic_link_email(to=email, link=link)
    await session.commit()


async def verify_magic_link(
    session: AsyncSession, token: str, login_state: str | None
) -> str | None:
    try:
        data = _serializer().loads(token, max_age=MAGIC_LINK_MAX_AGE)
    except (BadSignature, SignatureExpired):
        return None

    nonce = data.get("nonce")
    email = data.get("email")
    state = data.get("state")
    if not nonce or not email or not state:
        return None

    # Login-CSRF binding: the redeeming browser must present the same
    # login_state cookie that was set when the link was requested.
    if not login_state or not secrets.compare_digest(state, login_state):
        return None

    # Atomic conditional update: the WHERE clause and the used_at write are
    # one statement, so two concurrent redemptions of the same token cannot
    # both pass a separate "is it used?" check (TOCTOU) - exactly one wins
    # the row lock and gets a row back; the loser matches nothing.
    result = await session.execute(
        update(MagicLinkRequest)
        .where(
            MagicLinkRequest.nonce == nonce,
            MagicLinkRequest.email == email,
            MagicLinkRequest.used_at.is_(None),
        )
        .values(used_at=datetime.now(UTC))
        .returning(MagicLinkRequest.email)
    )
    redeemed_email = result.scalar_one_or_none()
    await session.commit()
    return redeemed_email
