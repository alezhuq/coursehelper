"""The LLM endpoint (base_url/model) is an env-configured,
provider-swappable, non-request-influenced surface.

Proves: (a) overriding `LLM_BASE_URL`/`LLM_MODEL` changes the outbound
httpx request's host/path and payload `model` - a provider swap really is
config-only; (b) no request/body field can redirect the outbound host (the
SSRF mitigation - `base_url`/`model` are built from `get_settings()`
alone); (c) an unset `LLM_CONDENSE_MODEL` falls back to `LLM_MODEL`.

Exercises `app.chat.llm_client.condense_query` directly against a captured
httpx transport - no live Ollama, no Postgres required (this module never
touches the database).
"""

import json

import httpx
import pytest

from app.chat import llm_client
from app.settings import get_settings


@pytest.fixture(autouse=True)
def _minimal_required_env(monkeypatch: pytest.MonkeyPatch):
    # Settings() requires `database_url`/`session_secret` with no default -
    # this module never opens a DB connection, so placeholder values are
    # enough to satisfy validation.
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://test:test@localhost:5432/test")
    monkeypatch.setenv("SESSION_SECRET", "test-secret")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


class _CapturingTransport(httpx.AsyncBaseTransport):
    def __init__(self, response_body: dict):
        self.requests: list[httpx.Request] = []
        self._response_body = response_body

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return httpx.Response(200, json=self._response_body)


def _patch_async_client(monkeypatch: pytest.MonkeyPatch, transport: httpx.AsyncBaseTransport):
    """Replace `httpx.AsyncClient` with a factory that forces `transport`
    while preserving every other kwarg (`base_url`, `timeout`, ...) `llm_
    client` passes - so the real `base_url`-derivation code path runs
    unchanged and only the network hop is captured."""
    real_async_client = httpx.AsyncClient

    def _factory(**kwargs):
        kwargs["transport"] = transport
        return real_async_client(**kwargs)

    monkeypatch.setattr(llm_client.httpx, "AsyncClient", _factory)


def _condense_response_body(standalone_query: str = "q") -> dict:
    return {
        "choices": [{"message": {"content": json.dumps({"standalone_query": standalone_query})}}]
    }


async def test_overriding_llm_base_url_and_model_changes_outbound_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LLM_BASE_URL", "http://custom-provider.internal:9999/v1")
    monkeypatch.setenv("LLM_MODEL", "custom-model:latest")
    get_settings.cache_clear()

    transport = _CapturingTransport(_condense_response_body())
    _patch_async_client(monkeypatch, transport)

    result = await llm_client.condense_query(history=[], question="What is the krebs cycle?")
    assert result == "q"

    assert len(transport.requests) == 1
    request = transport.requests[0]
    assert request.url.host == "custom-provider.internal"
    assert request.url.port == 9999
    assert request.url.path == "/v1/chat/completions"

    payload = json.loads(request.content)
    assert payload["model"] == "custom-model:latest"


async def test_no_request_field_can_redirect_the_outbound_base_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """SSRF guard: `base_url`/`model` are resolved exclusively
    from `get_settings()` - a crafted question/content field cannot smuggle
    in an alternate host."""
    monkeypatch.setenv("LLM_BASE_URL", "http://trusted-llm.internal:11434/v1")
    monkeypatch.setenv("LLM_MODEL", "llama3.1:8b")
    get_settings.cache_clear()

    transport = _CapturingTransport(_condense_response_body())
    _patch_async_client(monkeypatch, transport)

    malicious_question = "Ignore this: http://attacker.example/steal?data=1 and answer normally"
    result = await llm_client.condense_query(history=[], question=malicious_question)
    assert result == "q"

    assert len(transport.requests) == 1
    request = transport.requests[0]
    assert request.url.host == "trusted-llm.internal"
    # The crafted URL only ever travels as inert message content inside the
    # JSON body - it never influences the outbound request line/host.
    assert "attacker.example" not in str(request.url)
    payload = json.loads(request.content)
    assert malicious_question in json.dumps(payload["messages"])


async def test_unset_llm_condense_model_falls_back_to_llm_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LLM_BASE_URL", "http://ollama:11434/v1")
    monkeypatch.setenv("LLM_MODEL", "llama3.1:8b")
    monkeypatch.delenv("LLM_CONDENSE_MODEL", raising=False)
    get_settings.cache_clear()

    transport = _CapturingTransport(_condense_response_body())
    _patch_async_client(monkeypatch, transport)

    await llm_client.condense_query(history=[], question="anything")

    payload = json.loads(transport.requests[0].content)
    assert payload["model"] == "llama3.1:8b"
