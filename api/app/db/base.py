"""Async SQLAlchemy engine, session factory, and declarative base.

Every future model (users, courses, enrollments, books, chunks, ...) inherits
from `Base`. `get_session` is the FastAPI dependency used to obtain a scoped
`AsyncSession` per request.

The engine/session-factory are built lazily on first use, not eagerly at
import time: eagerly reading `settings.database_url` at module-import time
means whichever module happens to trigger the *first* import of this file
permanently fixes the connection URL for the process, even if a later test
fixture (e.g. `postgres_container`) sets the real `DATABASE_URL` env var
afterwards. Deferring construction until the first actual `get_session()`
call ensures env vars set by test fixtures are always picked up correctly,
regardless of import order across test modules.
"""

from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from app.settings import get_settings


class Base(DeclarativeBase):
    pass


_session_factory: async_sessionmaker[AsyncSession] | None = None


def _get_session_factory() -> async_sessionmaker[AsyncSession]:
    global _session_factory
    if _session_factory is None:
        engine = create_async_engine(get_settings().database_url, pool_pre_ping=True)
        _session_factory = async_sessionmaker(engine, expire_on_commit=False)
    return _session_factory


def async_session_factory() -> AsyncSession:
    """Return a new `AsyncSession` bound to the lazily-built engine."""
    return _get_session_factory()()


async def get_session() -> AsyncGenerator[AsyncSession, None]:
    async with async_session_factory() as session:
        yield session
