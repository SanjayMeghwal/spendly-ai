"""Schemas for the token endpoint.

The field names here are not ours to choose. `access_token` and `token_type`
are fixed by RFC 6749 (OAuth 2.0), which is what FastAPI's security machinery
and the "Authorize" button in /docs expect to find. Renaming them to something
tidier would break both.
"""

from typing import Literal

from pydantic import BaseModel, Field


class Token(BaseModel):
    """A successful login response."""

    access_token: str = Field(
        description="Signed JWT. Send it as: Authorization: Bearer <token>",
    )

    # Lowercase "bearer" is what RFC 6749 specifies. The header itself is
    # conventionally written "Bearer"; clients compare this field
    # case-insensitively.
    token_type: Literal["bearer"] = Field(
        default="bearer",
        description="Always 'bearer'.",
    )

    # Not required by the spec, but returning it saves every client from
    # decoding the JWT just to find out when to log in again - and decoding a
    # token client-side is exactly the habit that leads to clients *trusting*
    # its claims without verification.
    expires_in: int = Field(
        description="Seconds until the access token expires.",
        examples=[1800],
    )
