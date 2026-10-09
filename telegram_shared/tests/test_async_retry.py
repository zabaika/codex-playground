import asyncio

import pytest

from telegram_shared import async_retry


def test_awake_timeout_cancels_operation():
    cancelled = []

    async def operation():
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.append(True)

    async def trial():
        with pytest.raises(TimeoutError):
            await async_retry.await_with_awake_timeout(operation(), 0.01, cleanup_timeout_seconds=0.1)

    asyncio.run(trial())
    assert cancelled == [True]


def test_host_sleep_does_not_spend_timeout(monkeypatch):
    awake = [0.0]
    monkeypatch.setattr(async_retry, "awake_time", lambda: awake[0])

    async def operation():
        # More wall time than the deadline, but only a small amount of awake time.
        await asyncio.sleep(0.05)
        awake[0] = 0.005
        return "resumed"

    assert asyncio.run(async_retry.await_with_awake_timeout(
        operation(), 0.01, cleanup_timeout_seconds=0.1,
    )) == "resumed"


def test_external_cancellation_is_not_retried():
    calls = []

    async def operation():
        calls.append(True)
        raise asyncio.CancelledError()

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(async_retry.retry_async(
            operation, max_attempts=3, backoff_seconds=0, is_retryable=lambda exc: True,
        ))
    assert len(calls) == 1


def test_retry_exhaustion_and_permanent_error():
    for retryable, attempts in [(True, 3), (False, 1)]:
        calls = []

        async def operation():
            calls.append(True)
            raise ValueError("private provider payload")

        with pytest.raises(async_retry.AsyncRetryError) as caught:
            asyncio.run(async_retry.retry_async(
                operation, max_attempts=3, backoff_seconds=0, is_retryable=lambda exc: retryable,
            ))
        assert len(calls) == caught.value.attempts == attempts
        assert "private" not in str(caught.value)


def test_stuck_cancellation_is_distinct_from_retryable_timeout():
    async def trial():
        release = asyncio.Event()

        async def operation():
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                await release.wait()

        with pytest.raises(async_retry.AsyncCleanupError):
            await async_retry.await_with_awake_timeout(operation(), 0.01, cleanup_timeout_seconds=0.01)
        release.set()
        await asyncio.sleep(0)

    asyncio.run(trial())


@pytest.mark.parametrize("cleanup_error", [RuntimeError("cleanup failed"), TimeoutError("cleanup timed out")])
def test_cancellation_cleanup_exception_stops_retries(cleanup_error):
    calls = []

    async def operation():
        calls.append(True)
        try:
            await asyncio.Event().wait()
        finally:
            raise cleanup_error

    async def attempt():
        return await async_retry.await_with_awake_timeout(
            operation(), 0.01, cleanup_timeout_seconds=0.1,
        )

    with pytest.raises(async_retry.AsyncRetryError) as caught:
        asyncio.run(async_retry.retry_async(
            attempt, max_attempts=3, backoff_seconds=0,
            is_retryable=lambda exc: isinstance(exc, TimeoutError),
        ))
    assert len(calls) == caught.value.attempts == 1
    assert isinstance(caught.value.cause, async_retry.AsyncCleanupError)
    assert caught.value.cause.__cause__ is cleanup_error
