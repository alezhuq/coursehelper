from logging.config import fileConfig

from alembic import context
from sqlalchemy import create_engine, pool
from sqlalchemy.engine import Connection

from app.db.base import Base
from app.settings import get_settings

# Alembic Config object, provides access to values within alembic.ini.
config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Target metadata drives autogenerate; every model imports into Base.metadata
# by being imported somewhere reachable from here (added as models land).
target_metadata = Base.metadata

# Migrations run on a *sync* psycopg engine, not the app's async asyncpg
# engine. asyncpg always executes via prepared statements, which reject
# multi-statement scripts ("cannot insert multiple commands into a prepared
# statement") - and 0002_procrastinate_schema applies procrastinate's
# multi-statement schema.sql verbatim. psycopg (already a transitive
# dependency via procrastinate) executes plain SQL without that restriction,
# which is also Alembic's own recommended pattern for async apps.
sync_database_url = get_settings().database_url.replace("+asyncpg", "+psycopg", 1)
config.set_main_option("sqlalchemy.url", sync_database_url)


def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = create_engine(
        config.get_main_option("sqlalchemy.url"),
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        do_run_migrations(connection)

    connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
