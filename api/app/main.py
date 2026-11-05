"""FastAPI application entrypoint.

Wires `/health` (proves api -> db connectivity for the Docker Compose
healthcheck and the smoke test), auth (CAS/magic-link), the
role-request/approval routes, courses, books, hybrid search, and the RAG
chat routes.
"""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.routes import router as auth_router
from app.books.routes import router as books_router
from app.chat.routes import router as chat_router
from app.courses.routes import router as courses_router
from app.db.base import get_session
from app.ingestion.procrastinate_app import app as ingestion_app
from app.roles.routes import router as roles_router
from app.search.routes import router as search_router
from app.settings import get_settings

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    # Opens the procrastinate connector so upload/retry routes can
    # `defer_async` ingestion jobs; closed cleanly on shutdown.
    await ingestion_app.open_async()
    try:
        yield
    finally:
        await ingestion_app.close_async()


app = FastAPI(title="Student Course Library API", lifespan=lifespan)

app.include_router(auth_router)
app.include_router(roles_router)
app.include_router(courses_router)
app.include_router(books_router)
app.include_router(search_router)
app.include_router(chat_router)

if get_settings().enable_test_auth:
    # Test-only login bypass. ENABLE_TEST_AUTH
    # alone decides registration at startup, so it WOULD be exposed by a
    # production container that has the env var set - defense in depth:
    # refuse to start (loudly, not silently skipping) rather than serve a
    # total auth bypass in production.
    if get_settings().environment == "production":
        raise RuntimeError(
            "ENABLE_TEST_AUTH is set but ENVIRONMENT is production. "
            "/test-auth/login mints sessions for any user at any role; "
            "refusing to start with it enabled in production."
        )

    from app.testauth.routes import router as test_auth_router

    app.include_router(test_auth_router)


@app.get("/health")
async def health(session: AsyncSession = Depends(get_session)) -> JSONResponse:
    try:
        await session.execute(text("SELECT 1"))
    except Exception:
        logger.exception("Health check DB query failed")
        return JSONResponse(status_code=503, content={"status": "degraded"})
    return JSONResponse(status_code=200, content={"status": "ok", "db": "ok"})
