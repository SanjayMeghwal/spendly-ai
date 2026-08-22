"""SQLAlchemy ORM models.

Every model is re-exported here so that a single `from app import models`
registers all of them on `Base.metadata`.

This matters for Alembic: autogenerate can only see models that have been
imported. A model missing from this file is invisible to it, and Alembic will
conclude the table should not exist - generating a migration that DROPS it.
"""

from app.models.user import User

__all__ = ["User"]
