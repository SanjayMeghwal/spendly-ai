import uuid

import pytest
from fastapi import HTTPException

from app.core.rate_limit import UserRateLimiter


@pytest.mark.asyncio
async def test_rejects_after_limit_with_retry_after() -> None:
    limiter = UserRateLimiter()
    user_id = uuid.uuid4()

    await limiter.check(user_id, limit=2, window_seconds=60)
    await limiter.check(user_id, limit=2, window_seconds=60)

    with pytest.raises(HTTPException) as raised:
        await limiter.check(user_id, limit=2, window_seconds=60)

    assert raised.value.status_code == 429
    assert raised.value.headers == {"Retry-After": "60"}


@pytest.mark.asyncio
async def test_isolates_users() -> None:
    limiter = UserRateLimiter()
    user_a = uuid.uuid4()
    user_b = uuid.uuid4()

    await limiter.check(user_a, limit=1, window_seconds=60)
    await limiter.check(user_b, limit=1, window_seconds=60)


@pytest.mark.asyncio
async def test_discards_expired_requests(monkeypatch: pytest.MonkeyPatch) -> None:
    limiter = UserRateLimiter()
    user_id = uuid.uuid4()
    clock = [100.0]
    monkeypatch.setattr("app.core.rate_limit.time.monotonic", lambda: clock[0])

    await limiter.check(user_id, limit=1, window_seconds=60)
    with pytest.raises(HTTPException):
        await limiter.check(user_id, limit=1, window_seconds=60)
    clock[0] = 161.0
    await limiter.check(user_id, limit=1, window_seconds=60)
