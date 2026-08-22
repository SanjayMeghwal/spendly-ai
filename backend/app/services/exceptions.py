"""Domain exceptions raised by the service layer.

WHY NOT JUST RAISE HTTPException FROM A SERVICE?

Because `services/` must not import FastAPI. A service that raises
HTTPException can only ever be called from a web request - not from a CLI
command, a scheduled job, a test, or a background worker. It also inverts the
layering: business logic would be deciding HTTP status codes.

So services speak in domain terms ("this email is already registered") and the
API layer, which is the only layer that knows what HTTP is, translates each one
into a status code.
"""


class ServiceError(Exception):
    """Base class for every expected, domain-level failure.

    "Expected" is the operative word. These represent outcomes the business
    logic anticipates, not bugs. A programming error should still surface as an
    ordinary unhandled exception and a 500.
    """


class EmailAlreadyRegisteredError(ServiceError):
    """An account already exists with the submitted email address."""
