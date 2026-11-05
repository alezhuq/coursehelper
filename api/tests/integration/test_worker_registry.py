"""Regression guard: the procrastinate worker's ORM registry must be complete.

The worker CLI (`procrastinate --app app.ingestion.procrastinate_app.app worker`)
imports ``app.ingestion.procrastinate_app``, whose ``import_paths`` load only
``app.ingestion.tasks`` - which registers the ``Book`` model but *not* the
``User`` and ``Course`` tables that ``Book``'s foreign keys target. In the
worker's own process this left ``Base.metadata`` holding just ``books``, so the
first ``ingest_book_stub`` flush raised ``NoReferencedTableError`` and every
ingestion job failed before the ``queued -> processing -> ready`` transition.
The FastAPI process never hit this because ``app.main`` imports every domain's
routes (and therefore every model), incidentally completing the registry.

This test runs a *fresh* interpreter with only the worker's import surface -
the full pytest process imports every model via other test modules, which would
mask the bug - and asserts SQLAlchemy can resolve ``Book``'s foreign keys the
same way a flush does (``Mapper._sorted_tables``).
"""

import subprocess
import sys
import textwrap
from pathlib import Path


def test_worker_import_surface_resolves_book_foreign_keys() -> None:
    api_root = Path(__file__).resolve().parents[2]  # the api/ package root
    program = textwrap.dedent(
        """
        # Mirror the worker CLI's import surface exactly: it resolves
        # app.ingestion.procrastinate_app.app, and procrastinate's import_paths
        # then load app.ingestion.tasks. Neither imports User or Course unless
        # the registry is completed on purpose.
        import app.ingestion.procrastinate_app  # noqa: F401
        import app.ingestion.tasks  # noqa: F401
        from app.books.models import Book
        from app.db.base import Base

        # Accessing _sorted_tables is the exact Mapper operation a flush performs:
        # it resolves every foreign key's target Table and raises
        # NoReferencedTableError if a referenced model was never imported. This
        # line crashed the worker before the registry was completed.
        _ = Book.__mapper__._sorted_tables

        # And the foreign-key target tables must be registered in the shared
        # metadata (proves the aggregator pulled User/Course in, not just that
        # Book alone happens to resolve).
        missing = {"users", "courses"} - set(Base.metadata.tables)
        assert not missing, f"FK target tables missing from Base.metadata: {missing}"
        print("OK", sorted(Base.metadata.tables))
        """
    )
    result = subprocess.run(
        [sys.executable, "-c", program],
        capture_output=True,
        text=True,
        cwd=api_root,
    )
    assert result.returncode == 0, (
        "The isolated worker import surface cannot resolve Book's foreign keys "
        "(NoReferencedTableError - the worker registry is incomplete):\n"
        f"{result.stderr}"
    )
