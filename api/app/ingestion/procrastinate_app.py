"""Shared procrastinate application: the Postgres-backed ingestion job queue.

Consolidated into the api package rather than the standalone `worker/` package, because the task
body needs SQLAlchemy and the `Book` model, and the real ingestion
(Docling parse/chunk/embed) does too. Both the api (deferring jobs) and the
worker (executing them, `docker-compose.yml`'s `worker` service now runs
this image) import this single `app`.

`import_paths=["app.ingestion.tasks"]` auto-imports the task module so
`ingest_book_stub` is registered whenever this app is opened or the worker
CLI starts - no separate manual import needed at the CLI entrypoint.
"""

import os

import procrastinate

# Importing this aggregator registers every ORM model in Base.metadata. The
# worker only loads app.ingestion.tasks (via import_paths below), which pulls in
# Book but not the User/Course tables its foreign keys target; without a complete
# registry the first ingest_book_stub flush raises NoReferencedTableError. The
# api process gets a complete registry incidentally via app.main's route imports.
import app.db.models  # noqa: F401

# DATABASE_URL is the SQLAlchemy-style DSN (postgresql+asyncpg://...) shared
# with the rest of the api; psycopg's conninfo does not understand the
# "+asyncpg" driver suffix, so it is stripped for the connector.
#
# Read via `os.environ.get(...)` with a placeholder default, not
# `os.environ[...]`: this module is imported at test-collection time (via
# `app.ingestion.tasks`) before the `postgres_container` fixture has set the
# real `DATABASE_URL` env var. Constructing a `PsycopgConnector` does not
# connect, so a placeholder DSN here is safe - it mirrors `app/db/base.py`'s
# lazy-construction rationale.
_raw_dsn = os.environ.get("DATABASE_URL", "postgresql://localhost:5432/app")
_psycopg_dsn = _raw_dsn.replace("postgresql+asyncpg://", "postgresql://", 1)

app = procrastinate.App(
    connector=procrastinate.PsycopgConnector(conninfo=_psycopg_dsn),
    import_paths=["app.ingestion.tasks"],
)
