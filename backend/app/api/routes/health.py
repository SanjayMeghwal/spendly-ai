"""Liveness and readiness endpoints.

These serve orchestrators and load balancers, not application users, which is
why they sit at the root of the URL space rather than under /api/v1. A load
balancer has no notion of API versions.
"""

import asyncio
import logging

from fastapi import APIRouter, Response, status
from sqlalchemy import text

from app import __version__
from app.api.deps import DbSession, SettingsDep
from app.schemas.health import HealthResponse, ReadinessResponse

logger = logging.getLogger(__name__)

router = APIRouter(tags=["health"])

# A readiness probe must answer quickly. Orchestrators apply their own probe
# timeout, and a check that hangs is indistinguishable from a hung process -
# which gets the container killed rather than merely taken out of rotation.
READINESS_TIMEOUT_SECONDS = 5.0


@router.get(
    "/health",
    response_model=HealthResponse,
    summary="Liveness probe",
    description=(
        "Reports whether the process is alive. Checks no dependencies, so a "
        "database outage must never cause this to fail - that would make an "
        "orchestrator restart healthy containers during a transient blip."
    ),
)
async def health(settings: SettingsDep) -> HealthResponse:
    """Return liveness. Intentionally touches nothing external."""
    return HealthResponse(environment=settings.ENVIRONMENT, version=__version__)


@router.get(
    "/health/ready",
    response_model=ReadinessResponse,
    summary="Readiness probe",
    description=(
        "Reports whether this instance can serve traffic, by issuing a live "
        "`SELECT 1`. Returns 503 when the database is unreachable so that load "
        "balancers route around this instance instead of restarting it."
    ),
    responses={
        status.HTTP_503_SERVICE_UNAVAILABLE: {
            "description": "A dependency is unavailable.",
            "model": ReadinessResponse,
        }
    },
)
async def readiness(
    response: Response,
    db: DbSession,
    settings: SettingsDep,
) -> ReadinessResponse:
    """Verify database connectivity and report readiness.

    `SELECT 1` is used deliberately: it is the cheapest statement that proves
    the full path works - pool checkout, network, authentication, and the
    server's ability to answer - without touching application data.
    """
    try:
        await asyncio.wait_for(
            db.execute(text("SELECT 1")),
            timeout=READINESS_TIMEOUT_SECONDS,
        )
    except Exception:
        # A broad except is deliberate here, and this is one of the few places
        # it is correct. The endpoint's contract is "can this instance serve?",
        # so ANY failure means no.
        #
        # Catching only SQLAlchemyError was a real bug: an unreachable database
        # raises ConnectionRefusedError (an OSError) from the socket layer,
        # before any database-level error can exist, so SQLAlchemy never wraps
        # it. The probe returned 500 instead of 503 - telling an orchestrator
        # "this code is broken, restart me" rather than "my dependency is down,
        # route around me".
        #
        # Nothing is masked: the full traceback is logged for operators. The
        # response deliberately carries no detail, because database hostnames,
        # ports, and driver internals must not leak from a public endpoint.
        logger.exception("Readiness check failed: database unreachable")
        # 503, not 500: this is "temporarily unable to serve", not "bug in the
        # code". Load balancers and orchestrators act on that difference.
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return ReadinessResponse(
            status="degraded",
            database="unavailable",
            environment=settings.ENVIRONMENT,
            version=__version__,
        )

    return ReadinessResponse(
        status="ok",
        database="connected",
        environment=settings.ENVIRONMENT,
        version=__version__,
    )
