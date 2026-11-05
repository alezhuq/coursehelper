"""Custom-header CSRF guard for mutating routes.

Same-origin deployment (nginx proxies `/api/*` to this API under one
origin) already blocks most CSRF vectors via the session cookie's
`SameSite=Lax` attribute. As defense in depth, every mutating role-request
route also requires this custom header - a cross-site `<form>` POST has no
way to set an arbitrary request header, so its absence is a reliable CSRF
signal.

No token generation/storage/comparison is needed here: `SameSite=Lax` plus this
presence check is the
deliberately lightweight defense chosen for this app's same-origin threat
model; a full double-submit CSRF token library is not necessary.
"""

from fastapi import HTTPException, Request

CSRF_HEADER_NAME = "X-Requested-With"


async def require_custom_header(request: Request) -> None:
    """Reject a mutating request that lacks the custom CSRF header.

    Only presence is checked (not a specific value): a cross-origin form
    post cannot set this header at all, regardless of value, so presence
    alone is a reliable same-origin-fetch signal.
    """
    if not request.headers.get(CSRF_HEADER_NAME):
        raise HTTPException(status_code=403, detail="Missing required CSRF header")
