"""FastAPI application factory and entry point.

Run locally with:
    uv run uvicorn app.main:app --reload
"""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError

from app import __version__
from app.api.errors import validation_exception_handler
from app.api.routes import auth, budgets, health, transactions
from app.core.config import get_settings
from app.db.session import engine

settings = get_settings()

logging.basicConfig(
    level=logging.INFO if settings.ENVIRONMENT == "local" else logging.WARNING,
    format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    """Manage startup and shutdown.

    Code before `yield` runs once at startup; code after runs once at
    shutdown. This replaces the deprecated @app.on_event handlers, and unlike
    them it can hold resources open across the application's lifetime using an
    ordinary context manager.

    Disposing the engine on shutdown closes pooled connections cleanly.
    Skipping it leaves the database holding sockets open until they time out,
    which matters when containers restart frequently.
    """
    logger.info(
        "Starting %s v%s in %s mode", settings.PROJECT_NAME, __version__, settings.ENVIRONMENT
    )
    yield
    logger.info("Shutting down; disposing database connection pool")
    await engine.dispose()


app = FastAPI(
    title=settings.PROJECT_NAME,
    version=__version__,
    summary="AI-powered personal finance platform",
    lifespan=lifespan,
    # Interactive docs are disabled in production. They describe every
    # endpoint, parameter, and schema - useful in development, and useful to
    # an attacker mapping the attack surface.
    docs_url=None if settings.is_production else "/docs",
    redoc_url=None if settings.is_production else "/redoc",
    openapi_url=None if settings.is_production else "/openapi.json",
)

# Health routes are mounted at the ROOT, not under API_V1_PREFIX. They are
# consumed by load balancers and orchestrators, which have no concept of API
# versions. Business endpoints will be mounted under settings.API_V1_PREFIX so
# that a future /api/v2 can ship without breaking existing clients.
app.include_router(health.router)

# Business endpoints live under /api/v1 so that a future /api/v2 can ship
# without breaking existing clients.
app.include_router(auth.router, prefix=settings.API_V1_PREFIX)
app.include_router(transactions.router, prefix=settings.API_V1_PREFIX)
app.include_router(budgets.router, prefix=settings.API_V1_PREFIX)

# Replaces FastAPI's default 422 handler, which serialises pydantic's error
# list verbatim - including an "input" key holding the REJECTED VALUE. For a
# rejected password that means echoing the plaintext back to the client and
# into every log that records response bodies. See app/api/errors.py.
app.add_exception_handler(RequestValidationError, validation_exception_handler)
