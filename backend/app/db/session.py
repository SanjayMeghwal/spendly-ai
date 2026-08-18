"""Async database engine, session factory, and the per-request session dependency."""

from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.core.config import get_settings

settings = get_settings()

# ------------------------------------------------------------------------------
# Engine
#
# Created once at import time and shared for the process lifetime. The engine
# is NOT a connection - it owns a POOL of connections and is safe to share.
#
# Opening a PostgreSQL connection costs roughly 20-50ms (TCP handshake, auth,
# backend process spawn). Doing that per query would dominate response time,
# and Postgres caps concurrent connections (default max_connections = 100),
# so an unbounded approach would exhaust the server.
# ------------------------------------------------------------------------------
engine: AsyncEngine = create_async_engine(
    settings.database_url,
    # Log every statement. Useful while learning, far too noisy in production.
    echo=settings.ENVIRONMENT == "local",
    # Keep 5 connections warm; allow bursting to 15 before requests queue.
    pool_size=5,
    max_overflow=10,
    # Issue a cheap SELECT 1 before handing out a pooled connection. Without
    # this, a connection dropped by a network blip or a database restart is
    # handed to a request and fails with a confusing "server closed the
    # connection unexpectedly".
    pool_pre_ping=True,
    # Recycle connections older than 30 minutes, ahead of any proxy or
    # firewall idle timeout.
    pool_recycle=1800,
)

# ------------------------------------------------------------------------------
# Session factory
#
# expire_on_commit=False is REQUIRED in async code.
# By default SQLAlchemy expires loaded objects after commit, so the next
# attribute access silently re-queries the database. In async that hidden I/O
# happens outside an await context and raises MissingGreenlet. Disabling
# expiry keeps attributes readable after commit.
#
# autoflush=False makes writes explicit. With autoflush on, a mere SELECT can
# emit pending INSERTs as a side effect, which is surprising when debugging.
# ------------------------------------------------------------------------------
AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False,
)


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """Yield a database session scoped to a single request.

    A SQLAlchemy Session holds an identity map and an open transaction, and is
    NOT safe to share between concurrent requests. Sharing one would let an
    uncommitted change from one request be committed by another - in a finance
    application, that is corrupted transaction data.

    FastAPI calls this per request, and resumes it after the response is sent.
    The `async with` block guarantees the session is closed and any open
    transaction rolled back, even if the handler raises.

    Transaction boundaries are owned by the service layer: services call
    `await session.commit()` explicitly. This dependency deliberately does not
    auto-commit, so that what gets persisted is always visible in the code
    that intended it.

    Usage:
        @router.get("/items")
        async def list_items(db: AsyncSession = Depends(get_db)) -> ...:
            ...
    """
    async with AsyncSessionLocal() as session:
        yield session
