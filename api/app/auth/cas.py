"""SFU CAS wrapper: login-URL construction and server-side ticket validation.

A CAS ticket arriving on /auth/cas/callback is an
untrusted query param. The ONLY way it becomes trusted is a server-side GET to
`{cas_base_url}/serviceValidate` here - never a client-supplied "validated"
flag.

CAS 2.0 `/serviceValidate` is the deliberate baseline: it is
universally supported and returns the one thing auth needs, the SFU computing
id. CAS 3.0's `/p3/serviceValidate` attributes are an
optional enhancement and are never required - `CASClientV2.verify_response`
already tolerates (and we ignore) any attributes present.

The network hop uses httpx with an explicit `cas_validate_timeout_seconds`
timeout so an unreachable CAS raises `CasUnavailableError` instead of hanging;
the security-critical XML interpretation is delegated to python-cas's
`CASClientV2.verify_response` (pinned and reviewed at exactly 1.7.2).
"""

from urllib.parse import urlencode

import httpx
from cas import CASClientV2

from app.settings import get_settings

# Test seam: the `fake_cas_server` fixture (tests/conftest.py) installs an
# httpx.MockTransport here so integration tests exercise this module's real
# request/timeout/parse code against canned serviceValidate XML without a
# network socket. Production code never sets this (None = httpx default
# transport).
_transport: httpx.AsyncBaseTransport | None = None


class CasUnavailableError(Exception):
    """cas.sfu.ca timed out, was unreachable, or returned a 5xx.

    Callers must map this to a distinct degraded response (503 with the
    `cas_unavailable` code) so the login page can direct the user to the
    always-visible magic-link fallback - never a hang or a generic 500.
    """


def build_cas_login_url(service_url: str) -> str:
    """Return the CAS login redirect target for the browser.

    CAS will authenticate the user and redirect back to `service_url` with a
    single-use `ticket` query param.
    """
    settings = get_settings()
    return f"{settings.cas_base_url}/login?{urlencode({'service': service_url})}"


async def validate_ticket(ticket: str, service_url: str) -> str | None:
    """Validate a CAS ticket server-side; return the SFU computing id.

    Returns None when CAS rejects the ticket (authenticationFailure - e.g.
    forged, expired, or replayed: CAS tickets are single-use server-side).
    Raises CasUnavailableError on timeout, transport failure, or a CAS 5xx.
    """
    settings = get_settings()
    url = f"{settings.cas_base_url}/serviceValidate"
    try:
        async with httpx.AsyncClient(
            timeout=settings.cas_validate_timeout_seconds,
            transport=_transport,
        ) as client:
            response = await client.get(url, params={"ticket": ticket, "service": service_url})
    except httpx.TimeoutException as exc:
        raise CasUnavailableError("CAS ticket validation timed out") from exc
    except httpx.TransportError as exc:
        raise CasUnavailableError("CAS server unreachable") from exc

    if response.status_code >= 500:
        raise CasUnavailableError(f"CAS server returned {response.status_code}")

    user, _attributes, _pgtiou = CASClientV2.verify_response(response.content)
    return user
