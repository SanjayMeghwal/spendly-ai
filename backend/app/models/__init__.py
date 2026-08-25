"""SQLAlchemy ORM models.

Importing every model here serves one critical purpose beyond convenience:
Alembic's autogenerate only sees models whose modules have been IMPORTED,
because a model class registers itself on `Base.metadata` at import time.

`alembic/env.py` imports this package. A model that is never imported is
invisible to autogenerate - and since Alembic then sees a table in the
database with no matching model, it generates a DROP for it.

Add every new model module to this file.
"""

from app.models.refresh_token import RefreshToken
from app.models.transaction import Transaction
from app.models.user import User

# Re-exported names. Listing them explicitly tells linters these imports are
# intentional rather than unused, and documents the package's public surface.
__all__ = ["RefreshToken", "Transaction", "User"]
