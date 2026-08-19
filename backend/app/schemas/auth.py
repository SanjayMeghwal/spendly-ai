"""Request and response schemas for authentication."""

from pydantic import BaseModel, EmailStr, Field, SecretStr, field_validator


class LoginRequest(BaseModel):
    """Login credentials."""

    # JSON with an `email` field, rather than OAuth2's form-encoded
    # `username`. The rest of this API is JSON and the field genuinely is an
    # email address, so calling it `username` would be a lie kept only for
    # compatibility with a flow we do not implement.
    email: EmailStr
    password: SecretStr

    # NOTE deliberately NO length constraints here, unlike UserCreate.
    #
    # Validating the password length on LOGIN would reject a too-short attempt
    # with a 422 while a wrong-but-well-formed one gets a 401 - handing an
    # attacker a free oracle for the password POLICY, and a way to distinguish
    # request shapes without an account. Length is a REGISTRATION rule. At
    # login there is exactly one answer for every bad credential.

    @field_validator("email")
    @classmethod
    def normalise_email(cls, value: str) -> str:
        """Match the normalisation applied at registration.

        Stored addresses are lowercase, enforced by a CHECK constraint. Log in
        as "Ada@Example.com" without folding case here and the lookup finds
        nothing - a correct password would be rejected purely because of how
        the address was typed.
        """
        return value.strip().lower()


class TokenResponse(BaseModel):
    """An issued access token."""

    access_token: str

    # "bearer" is the RFC 6750 scheme name, and it is snake_case here because
    # the OAuth2 spec defines the wire format as `token_type`. Clients are
    # written against that name, so it is not ours to prettify.
    #
    # "Bearer" is literal: possession is sufficient. The token carries no
    # binding to a device, IP, or session, so anyone who obtains it IS the
    # user until it expires. That is precisely why expiry is short and why
    # the token must never be logged.
    token_type: str = Field(default="bearer")

    # Seconds until expiry, so a client can refresh ahead of time rather than
    # discovering the token is dead mid-request. Standard OAuth2 field.
    expires_in: int
