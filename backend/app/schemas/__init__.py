"""Pydantic schemas: the shape of API requests and responses.

Deliberately separate from `app/models/` (SQLAlchemy). Models describe what we
STORE; schemas describe what we EXPOSE. Keeping them apart is what stops a
password hash from reaching a client by accident - leaking one would require
deliberately adding it to a schema, not merely forgetting to remove it.
"""
