"""D4 (the empty-retrieval gate, CHAT-03): zero fused chunks yields exactly
the refusal string with NO generation call ever made - `is_empty_retrieval`
is the code-level guarantee (not a prompted behavior) that the model can
never invent an answer when nothing relevant was retrieved.
"""

import json
import uuid

import pytest
import pytest_asyncio
from httpx import AsyncClient
from testcontainers.postgres import PostgresContainer

from app.chat.prompts import REFUSAL_MESSAGE
from app.courses.models import Course
from app.db.base import async_session_factory
from app.enrollments.models import Enrollment
from app.users.models import Role, User
from app.users.repo import upsert_user

CSRF_HEADERS = {"X-Requested-With": "fetch"}


def _unit_vector(dim: int, hot_index: int) -> list[float]:
    vec = [0.0] * dim
    vec[hot_index] = 1.0
    return vec


async def _make_user(session, email: str, role: Role = Role.professor) -> User:
    user = await upsert_user(
        session,
        email=email,
        computing_id=email.split("@")[0],
        auth_method="cas",
        display_name=email.split("@")[0],
    )
    user.role = role
    await session.flush()
    return user


async def _make_course(session, owner: User, code: str) -> Course:
    course = Course(owner_id=owner.id, code=code, title="Test Course", term="Fall 2026")
    session.add(course)
    await session.flush()
    return course


async def _login(client: AsyncClient, computing_id: str, role: str) -> None:
    response = await client.post(
        "/test-auth/login", json={"computing_id": computing_id, "role": role}
    )
    assert response.status_code == 200


@pytest_asyncio.fixture(autouse=True)
def _mock_embed_batch(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "app.chat.routes.embed_batch", lambda texts: [_unit_vector(1024, 0) for _ in texts]
    )


@pytest_asyncio.fixture
async def empty_course_seeded(postgres_container: PostgresContainer):
    """An enrolled student in a course with zero indexed chunks - retrieval
    always fuses to zero ids regardless of the question asked."""
    async with async_session_factory() as session:
        owner = await _make_user(session, "emptygate-prof@sfu.ca")
        course = await _make_course(session, owner, "BISC 220")
        student = await _make_user(session, "emptygate-student@sfu.ca", Role.student)
        session.add(Enrollment(user_id=student.id, course_id=course.id))
        await session.commit()
        return {"course_id": course.id}


async def _get_conversation_id(async_client: AsyncClient, course_id: uuid.UUID) -> uuid.UUID:
    response = await async_client.get(f"/courses/{course_id}/chat/conversation")
    assert response.status_code == 200
    return uuid.UUID(response.json()["conversation_id"])


async def _post_message(
    async_client: AsyncClient, course_id: uuid.UUID, conversation_id: uuid.UUID, content: str
):
    return await async_client.post(
        f"/courses/{course_id}/chat/conversations/{conversation_id}/messages",
        json={"content": content},
        headers=CSRF_HEADERS,
    )


def _parse_sse(text: str):
    events = []
    for block in text.strip("\n").split("\n\n"):
        if not block.strip():
            continue
        event_name = None
        data_lines = []
        for line in block.splitlines():
            if line.startswith("event:"):
                event_name = line[len("event:") :].strip()
            elif line.startswith("data:"):
                data_lines.append(line[len("data:") :].strip())
        data_raw = "\n".join(data_lines)
        data = json.loads(data_raw) if data_raw else None
        events.append((event_name, data))
    return events


async def test_empty_retrieval_yields_exact_refusal_with_no_generation_call(
    async_client: AsyncClient,
    postgres_container: PostgresContainer,
    empty_course_seeded: dict,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    called = {"value": False}

    def _spy_stream_generation(*_args, **_kwargs):
        called["value"] = True
        raise AssertionError("stream_generation must not be called on empty retrieval")

    monkeypatch.setattr("app.chat.routes.stream_generation", _spy_stream_generation)

    await _login(async_client, "emptygate-student", "student")
    conversation_id = await _get_conversation_id(async_client, empty_course_seeded["course_id"])

    response = await _post_message(
        async_client, empty_course_seeded["course_id"], conversation_id, "Explain photosynthesis"
    )
    assert response.status_code == 200

    events = _parse_sse(response.text)
    token_events = [data for event, data in events if event == "token"]
    done_events = [event for event, _ in events if event == "done"]
    citation_events = [event for event, _ in events if event == "citations"]

    assert token_events == [REFUSAL_MESSAGE]
    assert done_events == ["done"]
    assert citation_events == []
    assert called["value"] is False
