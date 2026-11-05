"""Unit tests for magic-link @sfu.ca domain restriction.

Domain rejection must happen before any DB write or email send - asserted
here at the unit level (no DB/network fixtures) by calling
`request_magic_link` against a non-@sfu.ca address and expecting it to raise
before ever touching the repo/email layer.
"""

import os

import pytest

os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://test:test@localhost/test")
os.environ.setdefault("SESSION_SECRET", "test-session-secret")
os.environ.setdefault("APP_BASE_URL", "http://localhost:8080")

from app.auth.magic_link import InvalidMagicLinkDomain, request_magic_link  # noqa: E402


@pytest.mark.asyncio
async def test_non_sfu_email_rejected_before_any_side_effect() -> None:
    # Domain rejection happens before the session/DB is ever touched, so a
    # pure unit test can pass `None` in place of a real AsyncSession and
    # still exercise the exact validation path used by the route.
    with pytest.raises(InvalidMagicLinkDomain):
        await request_magic_link(None, "student@gmail.com")


@pytest.mark.parametrize(
    "email",
    ["a@sfu.ca.evil.com", "a@notsfu.ca", "a@sfu.ca ", "a@SFU.CA.attacker.io"],
)
@pytest.mark.asyncio
async def test_domain_check_rejects_lookalike_domains(email: str) -> None:
    with pytest.raises(InvalidMagicLinkDomain):
        await request_magic_link(None, email)
