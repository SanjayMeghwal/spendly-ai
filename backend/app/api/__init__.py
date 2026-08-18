"""HTTP layer: routers, request handling, status codes.

This layer translates HTTP to and from the service layer and MUST NOT contain
business logic. If a rule would still be true for a CLI or a scheduled job, it
belongs in `app/services/`, not here.
"""
