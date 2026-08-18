"""Database infrastructure: declarative base, engine, and session management.

This layer knows about SQLAlchemy and nothing about HTTP. Route handlers and
services depend on it; it depends on nothing above it.
"""
