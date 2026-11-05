"""Unit tests for the signed session cookie.

The cookie carries only a user id - no role, no email - so
`get_current_user` can always re-fetch live role/state from the DB rather
than trusting a stale claim baked into the cookie at issue time.
"""

import os
import uuid

os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://test:test@localhost/test")
os.environ.setdefault("SESSION_SECRET", "test-session-secret")
os.environ.setdefault("APP_BASE_URL", "http://localhost:8080")

from app.auth.session import issue_session_cookie, read_session_cookie  # noqa: E402


def test_session_cookie_round_trips_user_id() -> None:
    user_id = str(uuid.uuid4())

    token = issue_session_cookie(user_id)
    result = read_session_cookie(token)

    assert result == user_id


def test_tampered_session_cookie_returns_none() -> None:
    user_id = str(uuid.uuid4())
    token = issue_session_cookie(user_id)

    # Tamper a character inside the payload segment (before the first ".")
    # rather than the token's last character. The signature segment is
    # base64url-encoded HMAC-SHA1 output, and base64url's final character
    # only encodes a partial byte's worth of bits - roughly 15% of the time,
    # flipping the last character decodes to the same signature bytes, so
    # the tamper goes undetected and the test flakes. Tampering a middle
    # character of the payload instead changes the actual signed content,
    # so the signature check deterministically fails.
    payload, _, rest = token.partition(".")
    index = len(payload) // 2
    tampered_char = "a" if payload[index] != "a" else "b"
    tampered_payload = payload[:index] + tampered_char + payload[index + 1 :]
    tampered = f"{tampered_payload}.{rest}"

    assert read_session_cookie(tampered) is None


def test_garbage_session_cookie_returns_none() -> None:
    assert read_session_cookie("not-a-real-token") is None
