"""Small process-local rate limiter for expensive authenticated operations."""

import asyncio
import time
from collections import defaultdict, deque
from math import ceil
from uuid import UUID

from fastapi import HTTPException, status


class UserRateLimiter:
    """Enforce a sliding-window request limit keyed by authenticated user."""

    def __init__(self) -> None:
        self._requests: dict[UUID, deque[float]] = defaultdict(deque)
        self._lock = asyncio.Lock()

    async def check(self, user_id: UUID, *, limit: int, window_seconds: int) -> None:
        now = time.monotonic()
        cutoff = now - window_seconds

        async with self._lock:
            timestamps = self._requests[user_id]
            while timestamps and timestamps[0] <= cutoff:
                timestamps.popleft()

            if len(timestamps) >= limit:
                retry_after = max(1, ceil(timestamps[0] + window_seconds - now))
                raise HTTPException(
                    status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                    detail="Too many AI requests. Please try again later.",
                    headers={"Retry-After": str(retry_after)},
                )

            timestamps.append(now)

    async def clear(self) -> None:
        """Clear state for tests and controlled application maintenance."""
        async with self._lock:
            self._requests.clear()


ai_rate_limiter = UserRateLimiter()
