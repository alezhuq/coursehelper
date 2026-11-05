"""Aggregate import of every ORM model so the full mapper registry is populated.

Importing this module registers every domain's tables in ``Base.metadata`` as a
side effect. Processes that build the FastAPI app get this for free - ``app.main``
imports each domain's routes, which transitively import their models. But any
process that talks to the database *without* importing the routes needs an
explicit way to complete the registry, or SQLAlchemy raises
``NoReferencedTableError`` when it resolves a foreign key (e.g.
``books.uploaded_by -> users.id``) to a table whose model was never imported.

The procrastinate worker is exactly such a process: it only loads
``app.ingestion.tasks`` (via ``import_paths``), which registers ``Book`` but not
the ``User``/``Course`` tables its foreign keys target. ``app.ingestion.procrastinate_app``
imports this module so the worker's registry is complete before any task runs.

When a new model module lands, add its import here.
"""

from app.books import models as books_models  # noqa: F401
from app.chunks import models as chunks_models  # noqa: F401
from app.courses import models as courses_models  # noqa: F401
from app.enrollments import models as enrollments_models  # noqa: F401
from app.roles import models as roles_models  # noqa: F401
from app.users import models as users_models  # noqa: F401
