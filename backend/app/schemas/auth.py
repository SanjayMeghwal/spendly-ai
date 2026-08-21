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
    """A newly issued pair of tokens."""

    access_token: str

    # THE TWO TOKENS ARE NOT INTERCHANGEABLE, and the difference is the whole
    # design:
    #
    #   access_token  - short-lived (minutes), sent on EVERY request, cannot
    #                   be revoked. Its lifetime is its only containment.
    #   refresh_token - long-lived (days), sent ONLY to /auth/refresh, backed
    #                   by a database row that can be switched off.
    #
    # Returned in the JSON body rather than as an httpOnly cookie. That is the
    # right call for a JSON API with no browser client yet, and it has an
    # honest cost: a token in the body must be stored by the client, and
    # anything reachable from JavaScript is reachable by an XSS payload. When
    # the React frontend arrives, moving this into a Secure, httpOnly,
    # SameSite cookie - with the CSRF protection that then becomes necessary -
    # is a decision to make deliberately, not to inherit from today.
    refresh_token: str

    # "bearer" is the RFC 6750 scheme name, and it is snake_case here because
    # the OAuth2 spec defines the wire format as `token_type`. Clients are
    # written against that name, so it is not ours to prettify.
    #
    # "Bearer" is literal: possession is sufficient. The token carries no
    # binding to a device, IP, or session, so anyone who obtains it IS the
    # user until it expires. That is precisely why expiry is short and why
    # the token must never be logged.
    token_type: str = Field(default="bearer")

    # Seconds until the ACCESS token expires - not the refresh token. Named by
    # the OAuth2 spec, which defines it as the lifetime of `access_token`, so
    # a client can refresh ahead of time rather than discovering the token is
    # dead mid-request.
    #
    # The refresh token's expiry is deliberately not published. A client
    # cannot usefully act on it: rotation replaces the token on every use, and
    # the session can be revoked at any moment regardless of what the clock
    # says. Telling a client a date we may not honour invites it to trust one.
    expires_in: int


class RefreshRequest(BaseModel):
    """A refresh token presented as the credential.

    Shared by /auth/refresh, which spends it, and /auth/logout, which revokes
    it. One schema rather than two identical ones: the body is the same
    credential in the same place, and duplicating it would invite the two to
    drift apart in the OpenAPI document for no reason.
    """

    # SecretStr for the same reason as a password: this IS a credential, and a
    # 30-day one. Without it, any log line, debugger frame, or error-tracker
    # payload that renders the request model prints a working session key in
    # plaintext.
    #
    # Sent in the BODY rather than in an Authorization header, deliberately.
    # The header is where the ACCESS token lives, and a client that habitually
    # attaches its refresh token to requests would eventually attach it to the
    # wrong one - spraying its most valuable credential across every endpoint,
    # every proxy log, and every APM trace. Keeping it in the body of exactly
    # one endpoint keeps its blast radius to that endpoint.
    refresh_token: SecretStr
