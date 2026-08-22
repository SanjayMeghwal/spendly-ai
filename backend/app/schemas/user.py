"""Request and response schemas for users.

These types are the API CONTRACT. They exist separately from `app/models/` on
purpose: the model describes what we store, the schema describes what we are
willing to accept and reveal. Conflating them is how a `hashed_password` ends
up in a JSON response.
"""

import uuid
from datetime import datetime
from typing import Annotated

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

from app.core.security import MAX_PASSWORD_LENGTH

# NIST SP 800-63B: length is what makes a password strong, and composition
# rules ("one uppercase, one digit, one symbol") measurably do not. They push
# people toward predictable mutations - Password1! - and toward writing the
# result down. So we require length and nothing else.
MIN_PASSWORD_LENGTH = 8


class UserBase(BaseModel):
    """Fields shared by input and output schemas."""

    email: EmailStr = Field(
        description="Email address. Stored lowercase; used to log in.",
        examples=["ada@example.com"],
    )
    full_name: str | None = Field(
        default=None,
        max_length=255,
        description="Optional display name.",
        examples=["Ada Lovelace"],
    )

    @field_validator("email")
    @classmethod
    def _normalise_email(cls, v: str) -> str:
        """Lowercase and strip the address before it reaches the database.

        A UNIQUE index compares bytes, so without this "Ada@example.com" and
        "ada@example.com" are two different rows - the same person, two
        accounts, and a login that succeeds against whichever they happened to
        type. Normalising at the edge means every layer below can assume
        addresses are already canonical.

        The database also enforces this with a CHECK constraint (see the User
        model): this validator is the convenience, the constraint is the
        guarantee.
        """
        return v.strip().lower()

    @field_validator("full_name")
    @classmethod
    def _blank_name_is_none(cls, v: str | None) -> str | None:
        """Treat a whitespace-only name as absent.

        Otherwise `"   "` is stored as a name, and every UI that checks
        `if user.full_name:` renders an invisible one.
        """
        if v is None:
            return None
        stripped = v.strip()
        return stripped or None


class UserCreate(UserBase):
    """Registration payload.

    The plaintext password appears HERE and nowhere else in the codebase - not
    in the ORM model, not in any response schema. It is hashed in the service
    layer and the plaintext is never persisted or logged.
    """

    password: Annotated[
        str,
        Field(
            min_length=MIN_PASSWORD_LENGTH,
            max_length=MAX_PASSWORD_LENGTH,
            description=(
                f"Between {MIN_PASSWORD_LENGTH} and {MAX_PASSWORD_LENGTH} characters. "
                "No composition rules - length is what matters."
            ),
            examples=["correct horse battery staple"],
        ),
    ]


class UserRead(UserBase):
    """A user as returned by the API.

    THIS SCHEMA IS A SECURITY BOUNDARY. It is an allow-list: only the fields
    named here can ever be serialised. `hashed_password` is absent, so even if
    a handler returns a fully-loaded ORM object, the hash cannot escape.

    Compare with returning the ORM object directly - then every column added in
    future is published to the API automatically, and the day someone adds
    `password_reset_token` it leaks without a single line of code changing.
    """

    # from_attributes lets pydantic read a SQLAlchemy object's attributes
    # instead of requiring a dict, so a handler can hand back a User model.
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID = Field(description="Stable identifier for this account.")
    is_active: bool = Field(description="False for a deactivated account, which cannot log in.")
    created_at: datetime = Field(description="When the account was created (UTC).")
