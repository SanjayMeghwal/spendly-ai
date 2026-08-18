"""Spendly AI backend application package.

The event loop policy is configured here, at package import time, because it
must be set before any event loop is created. Importing anything under `app.*`
therefore fixes it once for uvicorn, pytest, Alembic, and scripts alike.
See app/core/compat.py for the full explanation.
"""

from app.core.compat import configure_event_loop_policy

configure_event_loop_policy()
