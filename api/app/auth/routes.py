"""Auth routes: SFU CAS login/callback, magic-link request/verify,
current-user, logout.

CAS (primary) and magic-link (fallback) converge on the
same `users_repo.upsert_user` + `set_session_cookie` sequence, so both paths
issue the identical persistent session.
"""

import secrets
import smtplib

from fastapi import APIRouter, Cookie, Depends, HTTPException, Request, Response
from fastapi.responses import RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.cas import CasUnavailableError, build_cas_login_url, validate_ticket
from app.auth.magic_link import (
    LOGIN_STATE_COOKIE_NAME,
    MAGIC_LINK_MAX_AGE,
    InvalidMagicLinkDomain,
    MagicLinkRateLimited,
    request_magic_link,
    verify_magic_link,
)
from app.auth.schemas import CurrentUserResponse, MagicLinkRequestBody, MagicLinkVerifyBody
from app.auth.session import clear_session_cookie, get_current_user, set_session_cookie
from app.db.base import get_session
from app.settings import get_settings
from app.users import repo as users_repo
from app.users.models import User

router = APIRouter(prefix="/auth", tags=["auth"])


def _cas_service_url() -> str:
    """The browser-facing callback URL registered with SFU CAS.

    nginx proxies `{app_base_url}/api/*` to this API (stripping `/api`), so
    the URL CAS redirects back to - and the `service` param sent on both the
    login redirect and the serviceValidate call, which CAS requires to match
    exactly - is the `/api`-prefixed public form.
    """
    return f"{get_settings().app_base_url}/api/auth/cas/callback"


@router.get("/cas/login")
async def cas_login() -> RedirectResponse:
    """Redirect the browser to cas.sfu.ca's login page."""
    return RedirectResponse(url=build_cas_login_url(_cas_service_url()), status_code=302)


@router.get("/cas/callback")
async def cas_callback(
    request: Request,
    ticket: str | None = None,
    session: AsyncSession = Depends(get_session),
) -> RedirectResponse:
    """Validate the CAS ticket server-side, upsert the user, issue a session.

    The ticket query param is untrusted input; it is only believed after
    `validate_ticket` re-validates it against cas.sfu.ca.

    Accepted residual risk (login CSRF): unlike the magic-link flow,
    this callback has no state binding to the browser that initiated login -
    CAS requires the `service` URL to match exactly on redirect and
    serviceValidate, leaving nowhere standard to carry per-browser state.
    An attacker who obtains a fresh ticket for their OWN account and gets a
    victim's browser to load this URL signs the victim in as the attacker.
    CAS tickets are single-use and expire within seconds of issuance, so
    the window is tight; the magic-link flow (15-minute tokens) carries the
    state binding instead.
    """
    if ticket is None:
        raise HTTPException(status_code=401, detail="Missing CAS ticket")

    try:
        computing_id = await validate_ticket(ticket, _cas_service_url())
    except CasUnavailableError as exc:
        # Distinct degraded response: a browser
        # navigation is bounced back to the login page with ?cas=unavailable
        # so the UI can point at the always-visible magic-link fallback; a
        # JSON consumer gets the single {"detail": ...} error shape with a
        # machine-readable code, as a 503 (never a hang or generic 500).
        if "text/html" in request.headers.get("accept", ""):
            return RedirectResponse(
                url=f"{get_settings().app_base_url}/login?cas=unavailable",
                status_code=303,
            )
        raise HTTPException(
            status_code=503,
            detail={
                "code": "cas_unavailable",
                "message": (
                    "SFU CAS is currently unavailable. Use the @sfu.ca magic-link sign-in instead."
                ),
            },
        ) from exc

    if computing_id is None:
        raise HTTPException(status_code=401, detail="CAS authentication failed")

    user = await users_repo.upsert_user(
        session,
        email=f"{computing_id.lower()}@sfu.ca",
        computing_id=computing_id,
        auth_method="cas",
        display_name=computing_id,
    )
    await session.commit()

    response = RedirectResponse(url=get_settings().app_base_url, status_code=303)
    set_session_cookie(response, user.id)
    return response


@router.post("/magic-link/request", status_code=202)
async def request_magic_link_route(
    body: MagicLinkRequestBody,
    response: Response,
    session: AsyncSession = Depends(get_session),
) -> dict[str, str]:
    # Login-CSRF binding: a random state is embedded in the token
    # AND set as a cookie on the requesting browser; verification requires
    # both to match, so a link can only complete sign-in in the browser
    # that asked for it.
    login_state = secrets.token_urlsafe(16)
    try:
        await request_magic_link(session, body.email, login_state)
    except InvalidMagicLinkDomain as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except MagicLinkRateLimited as exc:
        raise HTTPException(status_code=429, detail=str(exc)) from exc
    except (smtplib.SMTPException, OSError) as exc:
        # The email.py contract: a relay/connection failure surfaces as a
        # request failure mapped to 502 here, never a silently-dropped email.
        raise HTTPException(
            status_code=502, detail="Could not send the sign-in email. Try again."
        ) from exc

    response.set_cookie(
        key=LOGIN_STATE_COOKIE_NAME,
        value=login_state,
        max_age=MAGIC_LINK_MAX_AGE,
        httponly=True,
        secure=get_settings().environment != "development",
        samesite="lax",
    )
    return {"status": "sent"}


@router.post("/magic-link/verify")
async def verify_magic_link_route(
    body: MagicLinkVerifyBody,
    response: Response,
    login_state: str | None = Cookie(default=None, alias=LOGIN_STATE_COOKIE_NAME),
    session: AsyncSession = Depends(get_session),
) -> dict[str, str]:
    """Redeem a magic-link token and issue a session - POST only.

    Deliberately not a GET: the emailed link lands on the frontend
    `/auth/verify` page, and only that page's explicit "Sign in" button
    POSTs the token here. Email link scanners (O365/Outlook prefetch)
    follow GETs in inbound mail, which would burn the single-use token -
    and receive a session cookie - before the user ever clicked.

    The login_state cookie must match the state embedded in the token
    (login-CSRF binding): a browser that never requested this link
    cannot be signed in by it. The missing-cookie case is rejected BEFORE
    the token is redeemed, so the rightful requester's link still works in
    the browser that asked for it.
    """
    if login_state is None:
        raise HTTPException(
            status_code=400,
            detail=(
                "This sign-in link must be opened in the same browser it was "
                "requested from. Request a new link and open it here."
            ),
        )

    email = await verify_magic_link(session, body.token, login_state)
    if email is None:
        raise HTTPException(status_code=400, detail="Invalid, expired, or already-used link")

    user = await users_repo.upsert_user(
        session,
        email=email,
        computing_id=None,
        auth_method="magic_link",
        display_name=email.split("@", 1)[0],
    )
    await session.commit()

    response.delete_cookie(key=LOGIN_STATE_COOKIE_NAME)
    set_session_cookie(response, user.id)
    return {"status": "signed_in"}


@router.get("/me", response_model=CurrentUserResponse)
async def me(current_user: User = Depends(get_current_user)) -> CurrentUserResponse:
    return CurrentUserResponse(
        id=str(current_user.id),
        email=current_user.email,
        display_name=current_user.display_name,
        role=current_user.role.value,
        auth_method=current_user.auth_method.value,
    )


@router.post("/logout")
async def logout(response: Response) -> dict[str, str]:
    # Local-only logout: clears just this app's session cookie. Never call
    # any CAS/external global-logout endpoint here - that would force-log the
    # user out of every other
    # CAS-integrated SFU service they have open.
    clear_session_cookie(response)
    return {"status": "logged_out"}
