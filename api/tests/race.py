"""Deterministic two-session race helper for get-or-create regression tests.

Pattern used by the race tests: session A performs the write without
committing; session B's call starts as an asyncio task and (if the code is
not race-safe) blocks on A's uncommitted unique key; this helper polls
`pg_stat_activity` from a third session until some backend is waiting on a
lock, which proves B reached the contended write; the test then commits A
and B must complete without raising.
"""

import asyncio

from sqlalchemy import text

from app.db.base import async_session_factory


async def wait_for_lock_wait(task: asyncio.Task, timeout: float = 5.0) -> None:
    """Poll until a backend shows `wait_event_type = 'Lock'`, or `task`
    finishes early (its outcome is surfaced when the caller awaits it)."""
    deadline = asyncio.get_running_loop().time() + timeout
    async with async_session_factory() as session:
        while asyncio.get_running_loop().time() < deadline:
            if task.done():
                return
            waiting = (
                await session.execute(
                    text(
                        "SELECT count(*) FROM pg_stat_activity "
                        "WHERE datname = current_database() AND wait_event_type = 'Lock'"
                    )
                )
            ).scalar_one()
            await session.rollback()
            if waiting:
                return
            await asyncio.sleep(0.05)
    raise AssertionError("no backend ever blocked on a lock within the timeout")
