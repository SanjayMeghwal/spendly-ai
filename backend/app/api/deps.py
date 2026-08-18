"""Shared FastAPI dependencies, exposed as reusable type aliases.

WHY ALIASES RATHER THAN INLINE Depends(...)

The older style puts the dependency in the default value:

    async def handler(db: AsyncSession = Depends(get_db)) -> ...:

That works, but it repeats the wiring in every handler, and it means the
parameter has a default - so no non-default parameter may follow it.

The `Annotated` form keeps the dependency in the TYPE rather than the default:

    async def handler(db: DbSession) -> ...:

Declared once here and reused everywhere. Handlers read as ordinary typed
functions, parameter ordering is unconstrained, and mypy checks them normally.
This is the form the FastAPI docs now recommend.
"""

from typing import Annotated

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.db.session import get_db

# A database session scoped to the current request.
DbSession = Annotated[AsyncSession, Depends(get_db)]

# Application settings. Injected rather than imported so tests can override it
# through app.dependency_overrides.
SettingsDep = Annotated[Settings, Depends(get_settings)]
