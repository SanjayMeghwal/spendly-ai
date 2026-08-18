"""Response schemas for the health and readiness endpoints."""

from typing import Literal

from pydantic import BaseModel, Field


class HealthResponse(BaseModel):
    """Liveness response. Deliberately contains no dependency information."""

    status: Literal["ok"] = Field(
        default="ok",
        description="Always 'ok'. If the process cannot answer at all, the "
        "request fails at the transport layer instead.",
    )
    environment: str = Field(description="Active environment: local, test, or production.")
    version: str = Field(description="Application version.")


class ReadinessResponse(BaseModel):
    """Readiness response: can this instance actually serve traffic?

    Returned with HTTP 503 when any dependency is unavailable, so that load
    balancers route around this instance rather than restarting it.
    """

    status: Literal["ok", "degraded"] = Field(
        description="'ok' when every dependency is reachable, otherwise 'degraded'."
    )
    database: Literal["connected", "unavailable"] = Field(
        description="Result of a live SELECT 1 against PostgreSQL."
    )
    environment: str = Field(description="Active environment.")
    version: str = Field(description="Application version.")
