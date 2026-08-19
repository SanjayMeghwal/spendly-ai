"""Alembic migration environment.

Adapted from the async template. Two things differ from the generated
default:

1. The database URL comes from application settings, not from alembic.ini.
   alembic.ini is committed to a public repository, so it must never hold
   credentials.

2. `target_metadata` points at our declarative Base, which is what makes
   `alembic revision --autogenerate` able to diff models against the live
   database.
"""

import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

# ------------------------------------------------------------------------------
# IMPORTANT - autogenerate only sees models that have been IMPORTED.
#
# A model class registers itself on Base.metadata when its module is imported.
# If a model is never imported here, autogenerate does not know it exists and
# will happily generate a migration that DROPS its table, because from
# Alembic's point of view the table exists in the database but not in the
# models.
#
# `app.models` imports every model module, so this single line keeps every
# table visible to autogenerate. Adding a new model means adding it to
# app/models/__init__.py, not editing this file again.
#
# noqa: F401 - the import is never referenced by name. It exists purely for
# its side effect of registering models on Base.metadata, which is exactly
# the kind of import a linter is right to be suspicious of.
# ------------------------------------------------------------------------------
from app import models  # noqa: F401
from app.core.config import get_settings
from app.db.base import Base

config = context.config

# Credentials come from the environment, never from alembic.ini.
# Escaping '%' protects the value from ConfigParser's string interpolation.
settings = get_settings()
config.set_main_option("sqlalchemy.url", settings.database_url.replace("%", "%%"))

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def _configure_context(connection: Connection) -> None:
    """Shared migration context options.

    compare_type detects column type changes (VARCHAR(50) -> VARCHAR(100)),
    which is off by default and silently omits real schema drift.

    compare_server_default detects changes to DEFAULT clauses. It is somewhat
    prone to false positives, which is acceptable: a spurious line in a
    generated migration is cheap to delete, whereas a missed change is not.
    """
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,
        compare_server_default=True,
    )


def run_migrations_offline() -> None:
    """Emit SQL to stdout instead of executing it.

    Used to hand a reviewable SQL script to a DBA, which is how schema changes
    are often applied in regulated environments.
    """
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        compare_server_default=True,
    )

    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    _configure_context(connection)
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    """Run migrations against a live database using an async engine.

    NullPool is used deliberately: a migration run is a short-lived process,
    so pooling connections for reuse serves no purpose and would only delay
    process exit.
    """
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)

    await connectable.dispose()


def run_migrations_online() -> None:
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
