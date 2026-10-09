"""Bounded async retries and timeouts that exclude host sleep."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import TypeVar

from common.process import awake_time


T = TypeVar("T")


class AsyncCleanupError(RuntimeError):
    """Cancellation did not finish; retrying could overlap live operations."""


class AsyncRetryError(RuntimeError):
    def __init__(self, cause: Exception, attempts: int, max_attempts: int):
        self.cause = cause
        self.attempts = attempts
        self.max_attempts = max_attempts
        super().__init__(f"{type(cause).__name__} (attempts: {attempts}/{max_attempts})")


async def await_with_awake_timeout(
    operation: Awaitable[T], timeout_seconds: float, *, cleanup_timeout_seconds: float,
) -> T:
    task = asyncio.ensure_future(operation)
    deadline = awake_time() + timeout_seconds
    try:
        while not task.done():
            remaining = deadline - awake_time()
            if remaining <= 0:
                raise TimeoutError("Async operation timed out")
            await asyncio.wait({task}, timeout=min(1.0, remaining))
        return task.result()
    finally:
        if not task.done():
            task.cancel()
            done, _ = await asyncio.wait({task}, timeout=cleanup_timeout_seconds)
            if not done:
                # Consume a late exception without claiming cleanup succeeded.
                task.add_done_callback(lambda t: t.exception() if not t.cancelled() else None)
                raise AsyncCleanupError("Async cancellation timed out")
            if not task.cancelled():
                cleanup_error = task.exception()
                if cleanup_error is not None:
                    raise AsyncCleanupError("Async cancellation cleanup failed") from cleanup_error


async def retry_async(
    operation: Callable[[], Awaitable[T]], *, max_attempts: int,
    backoff_seconds: float, is_retryable: Callable[[Exception], bool],
    on_failure: Callable[[Exception, int], None] | None = None,
    on_recovery: Callable[[int], None] | None = None,
) -> T:
    if max_attempts < 1 or backoff_seconds < 0:
        raise ValueError("Invalid async retry budget")
    for attempt in range(1, max_attempts + 1):
        try:
            result = await operation()
        except Exception as exc:
            if on_failure is not None:
                on_failure(exc, attempt)
            if attempt == max_attempts or not is_retryable(exc):
                raise AsyncRetryError(exc, attempt, max_attempts) from exc
            await asyncio.sleep(backoff_seconds)
        else:
            if attempt > 1 and on_recovery is not None:
                on_recovery(attempt)
            return result
    raise AssertionError("Unreachable retry state")
