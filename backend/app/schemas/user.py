"""Request and response schemas for users.

THIS FILE IS WHY models/ AND schemas/ ARE SEPARATE.

`app.models.User` describes what we STORE - including `hashed_password`.
The schemas here describe what we ACCEPT and what we EXPOSE, and they are
deliberately different shapes:

  - UserCreate has a `password` field the model does not have.
  - UserRead has no `hashed_password` field the model does have.

That second point is the important one. Because the route declares
`response_model=UserRead`, FastAPI serialises through this schema and silently
drops every attribute not declared on it. Leaking the password hash therefore
requires someone to ADD a field here on purpose - it cannot happen by
forgetting something. If we returned ORM objects directly, every new column
would be exposed by default, and the safety of the API would depend on
remembering. Structure beats vigilance.
"""

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field, SecretStr, field_validator

from app.core.security import MAX_PASSWORD_LENGTH, MIN_PASSWORD_LENGTH


class UserCreate(BaseModel):
    """Registration request body."""

    email: EmailStr = Field(
        description="Email address. Also the login identifier.",
        examples=["ada@example.com"],
    )

    # SecretStr, not str.
    #
    # It changes how the value RENDERS, not how it validates: repr() and
    # str() of the model show `SecretStr('**********')` instead of the
    # password. That matters because models end up in log lines, debugger
    # output, and error-tracker payloads, and a plaintext password reaching
    # any of those is a credential leak that no amount of hashing undoes.
    #
    # Reading it requires an explicit `.get_secret_value()`, which makes every
    # place the plaintext is genuinely handled visible in review.
    #
    # IMPORTANT - what SecretStr does NOT do: pydantic still includes the
    # rejected value under "input" in validation errors, even for SecretStr
    # (verified, not assumed). FastAPI's default 422 handler would return that
    # to the client. See app/api/errors.py, which strips it.
    password: SecretStr = Field(
        min_length=MIN_PASSWORD_LENGTH,
        max_length=MAX_PASSWORD_LENGTH,
        description=(
            f"Between {MIN_PASSWORD_LENGTH} and {MAX_PASSWORD_LENGTH} characters. "
            "Length matters far more than mandatory symbols; a passphrase is ideal."
        ),
    )

    full_name: str | None = Field(
        default=None,
        max_length=255,
        description="Optional display name.",
        examples=["Ada Lovelace"],
    )

    @field_validator("email")
    @classmethod
    def normalise_email(cls, value: str) -> str:
        """Lowercase and trim the address before it reaches the database.

        EmailStr validates the FORMAT but does not fold case in the local
        part, so "Ada@Example.com" arrives intact. Left alone it becomes a
        second row alongside "ada@example.com" - two accounts for one mailbox,
        and a login that depends on which row is found first.

        This is the first of two defences. The second is the CHECK constraint
        `email = lower(email)` on the table, which catches any code path that
        reaches the database without passing through this schema. This one
        produces a good user experience; that one produces the guarantee.
        """
        return value.strip().lower()


class UserRead(BaseModel):
    """A user account."""

    # NOTE - a pydantic model's DOCSTRING becomes the schema `description` in
    # the OpenAPI document, which is public API documentation rendered at
    # /docs. Internal reasoning therefore belongs in comments like this one,
    # not in the docstring above: consumers of our API have no need to know
    # what our columns are called, and naming them there is free reconnaissance.
    #
    # What matters about this model is what it does NOT declare:
    # `hashed_password`. FastAPI drops every attribute a response model does
    # not list, so the hash cannot reach a client unless someone adds a field
    # here deliberately.
    #
    # from_attributes lets pydantic build this from an ORM object's attributes
    # rather than a dict, which is what allows a route to return the SQLAlchemy
    # User directly and still be filtered through this schema.
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    email: EmailStr
    full_name: str | None
    is_active: bool
    created_at: datetime
