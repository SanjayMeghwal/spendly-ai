"""Application-wide exception handlers.

HTTP concerns only - this is the layer that turns a failure into a status code
and a response body.
"""

from fastapi import Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

# Keys copied from each pydantic error. Anything not listed is discarded.
#
# An ALLOWLIST, not a blocklist. A blocklist ("strip `input`") silently starts
# leaking again the day pydantic adds a new key that happens to carry the
# offending value. With an allowlist, a new key is simply not copied, so the
# default is safe and the failure mode of forgetting to update it is a
# less-informative error rather than a disclosure.
_SAFE_ERROR_KEYS = ("type", "loc", "msg")


async def validation_exception_handler(
    _request: Request,
    exc: Exception,
) -> JSONResponse:
    """Return a 422 that describes the problem without echoing the input.

    THE BUG THIS FIXES - verified empirically, not assumed.

    Pydantic includes the REJECTED VALUE under an "input" key in every
    validation error, and FastAPI's default handler serialises `exc.errors()`
    straight into the response body. So posting a too-short password to
    /register would come back as:

        {"detail": [{"type": "too_short", "loc": ["body", "password"],
                     "msg": "...", "input": "hunter2"}]}

    The user's plaintext password, returned over the wire and written into
    every access log, proxy log, and error tracker that records response
    bodies. Since people reuse passwords, a leak here is not limited to this
    application.

    Declaring the field as `SecretStr` does NOT prevent this. SecretStr
    changes how the value is REPRESENTED - repr() and str() mask it, which is
    worth having - but pydantic still reports the raw input in the error. Both
    defences are needed, and this is the one that closes the response leak.

    The signature takes `Exception` rather than `RequestValidationError`
    because that is the type FastAPI's `add_exception_handler` is annotated to
    accept; the isinstance check narrows it back for the type checker.
    """
    if not isinstance(exc, RequestValidationError):  # pragma: no cover - defensive
        raise exc

    scrubbed = [
        {key: error[key] for key in _SAFE_ERROR_KEYS if key in error} for error in exc.errors()
    ]

    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        content={"detail": scrubbed},
    )
