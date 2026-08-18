"""Platform compatibility shims.

Isolated here so platform-specific workarounds are discoverable in one place
rather than scattered across entry points.
"""

import asyncio
import sys


def configure_event_loop_policy() -> None:
    """Force a selector-based event loop on Windows.

    THE PROBLEM
    Python on Windows defaults to ProactorEventLoop, built on Windows IOCP.
    psycopg's async mode requires a selector-based loop and refuses to run,
    raising:

        psycopg.InterfaceError: Psycopg cannot use the 'ProactorEventLoop'
        to run in async mode.

    This surfaces as a connection failure buried under ~100 lines of
    SQLAlchemy pool internals, so it is worth naming explicitly.

    WHY IT LIVES HERE
    The policy must be set before ANY event loop is created. Calling this from
    `app/__init__.py` means it runs on the first import of anything under
    `app.*`, which covers uvicorn, pytest, Alembic, and ad-hoc scripts
    uniformly - rather than relying on every entry point remembering to do it.

    NOT NEEDED IN PRODUCTION
    Linux and macOS already default to a selector-compatible loop, so this is
    a no-op there. It exists purely for local development on Windows.

    TRADE-OFF
    SelectorEventLoop on Windows caps out around 512 sockets, which is
    irrelevant for local development. If that ever became a constraint, the
    alternatives are switching to the asyncpg driver or running the backend
    inside a Linux container.
    """
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
