"""Shared pytest fixtures: a real Postgres container and an ASGI test client.

`postgres_container` spins up `pgvector/pgvector:pg16` via testcontainers so
integration tests (auth/authz/search/chat)
exercise real Postgres behavior rather than mocks. `async_client` builds an
httpx AsyncClient over the FastAPI app using ASGITransport (no real network
socket needed).
"""

import os
from collections.abc import AsyncGenerator, Generator
from pathlib import Path
from typing import TYPE_CHECKING
from unittest.mock import MagicMock

import httpx
import pytest
import pytest_asyncio
from alembic import command
from alembic.config import Config
from httpx import ASGITransport, AsyncClient
from testcontainers.postgres import PostgresContainer

if TYPE_CHECKING:
    from procrastinate.testing import InMemoryConnector

API_ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(scope="session")
def postgres_container() -> AsyncGenerator[PostgresContainer, None]:
    with PostgresContainer("pgvector/pgvector:pg16", driver="asyncpg") as container:
        os.environ["DATABASE_URL"] = container.get_connection_url()
        os.environ.setdefault("SESSION_SECRET", "test-secret")
        os.environ.setdefault("APP_BASE_URL", "http://localhost:8080")
        os.environ.setdefault("ENABLE_TEST_AUTH", "true")
        # The api defaults ENVIRONMENT to "production" (fail-safe Secure
        # cookies). Tests drive the app over http://test, and httpx's
        # cookie jar - like a real browser - refuses to return Secure
        # cookies over plain http, so the test env must opt into dev mode.
        os.environ.setdefault("ENVIRONMENT", "development")

        # get_settings() is process-wide @lru_cache'd. Unit tests that run
        # earlier in the same pytest session (e.g. test_session_cookie.py)
        # call it before this fixture ever runs, caching a Settings
        # instance built from a placeholder DATABASE_URL (no real port).
        # Clear that cache now so every settings-dependent call from this
        # point on (this migration run, the app's lazy DB engine, etc.)
        # reads the real containerized URL just set above.
        from app.settings import get_settings

        get_settings.cache_clear()

        # Every model's real table must exist for integration tests to
        # exercise real Postgres behavior (this project's own testing
        # philosophy - see STACK.md's testcontainers rationale) rather than
        # assuming SQLAlchemy's create_all() matches the Alembic-managed
        # schema. env.py builds its own sync engine from DATABASE_URL, so
        # setting the env var above before this call is what wires it to
        # the container.
        alembic_cfg = Config(str(API_ROOT / "alembic.ini"))
        command.upgrade(alembic_cfg, "head")

        yield container


@pytest.fixture(autouse=True)
def _mock_smtp(monkeypatch: pytest.MonkeyPatch) -> None:
    """Stub out smtplib.SMTP so magic-link tests never require a real
    Mailpit/SMTP process. `app/auth/email.py` deliberately does not swallow
    SMTP errors, so without this stub every magic-link request would fail
    with a connection error in a plain test environment.
    """
    mock_smtp = MagicMock()
    mock_smtp.__enter__ = MagicMock(return_value=mock_smtp)
    mock_smtp.__exit__ = MagicMock(return_value=False)
    monkeypatch.setattr("smtplib.SMTP", MagicMock(return_value=mock_smtp))


_CAS_SUCCESS_XML = """<cas:serviceResponse xmlns:cas="http://www.yale.edu/tp/cas">
    <cas:authenticationSuccess>
        <cas:user>{user}</cas:user>
    </cas:authenticationSuccess>
</cas:serviceResponse>"""

_CAS_FAILURE_XML = """<cas:serviceResponse xmlns:cas="http://www.yale.edu/tp/cas">
    <cas:authenticationFailure code="INVALID_TICKET">
        Ticket not recognized
    </cas:authenticationFailure>
</cas:serviceResponse>"""


class FakeCasServer:
    """In-process fake of cas.sfu.ca's serviceValidate endpoint.

    Installed into `app.auth.cas._transport` as an httpx.MockTransport, so
    `validate_ticket`'s real request/timeout/XML-parse code runs unchanged
    against canned CAS 2.0 responses - no socket, no real SFU dependency.

    Modes:
      - "success": authenticationSuccess XML for `self.user`
      - "failure": authenticationFailure XML (invalid/replayed ticket)
      - "timeout": raises httpx.ConnectTimeout (CAS unreachable / hung)
      - "error":   HTTP 503 from CAS itself
    """

    def __init__(self) -> None:
        self.mode = "success"
        self.user = "jdoe"
        self.requests: list[httpx.Request] = []

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self._handle)

    def _handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.mode == "timeout":
            raise httpx.ConnectTimeout("fake CAS timed out", request=request)
        if self.mode == "error":
            return httpx.Response(503, text="fake CAS internal error")
        if self.mode == "failure":
            return httpx.Response(200, text=_CAS_FAILURE_XML)
        return httpx.Response(200, text=_CAS_SUCCESS_XML.format(user=self.user))


@pytest.fixture
def fake_cas_server(monkeypatch: pytest.MonkeyPatch) -> Generator[FakeCasServer, None, None]:
    from app.auth import cas as cas_module

    fake = FakeCasServer()
    monkeypatch.setattr(cas_module, "_transport", fake.transport())
    yield fake


@pytest_asyncio.fixture
async def async_client(postgres_container: PostgresContainer) -> AsyncGenerator[AsyncClient, None]:
    # Imported lazily so DATABASE_URL is set (via postgres_container) before
    # app.db.base builds its engine at import time.
    from app.main import app

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


@pytest_asyncio.fixture
async def ingestion_memory_connector(
    postgres_container: PostgresContainer,
) -> AsyncGenerator["InMemoryConnector", None]:
    """Swap the ingestion App's connector to an in-memory procrastinate
    connector so `defer_async`/`queueing_lock` tests run without a real
    worker or `procrastinate_jobs` rows.

    Imported lazily (after `postgres_container` has set `DATABASE_URL`) to
    respect this file's existing import-order discipline - `app.ingestion.
    procrastinate_app` reads `DATABASE_URL` at import time to build its
    (unconnected) `PsycopgConnector`.
    """
    from procrastinate.testing import InMemoryConnector

    from app.ingestion.procrastinate_app import app as ingestion_app

    test_connector = InMemoryConnector()
    with ingestion_app.replace_connector(test_connector):
        await test_connector.open_async()
        yield test_connector
        await test_connector.close_async()
