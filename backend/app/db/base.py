"""Declarative base class for all ORM models.

Every model in `app/models/` inherits from `Base`. The shared `MetaData` it
carries is what Alembic inspects to autogenerate migrations.
"""

from sqlalchemy import MetaData
from sqlalchemy.orm import DeclarativeBase

# ------------------------------------------------------------------------------
# Constraint naming convention
#
# WHY THIS MATTERS - set it now, not later:
# Without an explicit convention, PostgreSQL invents constraint names such as
# `users_email_key`. Alembic cannot reliably emit `DROP CONSTRAINT` in a
# downgrade because it does not know what name the database chose, so
# migrations become one-way.
#
# Defining the convention up front makes every constraint name deterministic
# and every migration reversible. Retrofitting it onto an existing schema
# means renaming every constraint by hand.
#
# Placeholders are filled in by SQLAlchemy:
#   %(table_name)s, %(column_0_name)s, %(referred_table_name)s, ...
# ------------------------------------------------------------------------------
NAMING_CONVENTION = {
    "ix": "ix_%(table_name)s_%(column_0_name)s",  # index
    "uq": "uq_%(table_name)s_%(column_0_name)s",  # unique constraint
    "ck": "ck_%(table_name)s_%(constraint_name)s",  # check constraint
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",  # primary key
}


class Base(DeclarativeBase):
    """Base class for every ORM model.

    Subclasses are automatically registered on `Base.metadata`, which is the
    single source of truth Alembic compares against the live database.
    """

    metadata = MetaData(naming_convention=NAMING_CONVENTION)
