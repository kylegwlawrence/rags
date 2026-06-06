"""Generic retry-with-exponential-backoff helper. Library-agnostic: caller passes the exception class."""

import time
from collections.abc import Callable
from typing import TypeVar

T = TypeVar("T")

MAX_ATTEMPTS = 3
BACKOFF_BASE = 2.0


def with_retry(fn: Callable[[], T], exc: type[BaseException]) -> T:
    """Call `fn()` up to MAX_ATTEMPTS times with exponential backoff. Re-raises on final failure."""
    last_exc: BaseException | None = None
    for attempt in range(MAX_ATTEMPTS):
        if attempt:
            time.sleep(BACKOFF_BASE**attempt)
        try:
            return fn()
        except exc as e:
            last_exc = e
    assert last_exc is not None  # MAX_ATTEMPTS >= 1
    raise last_exc
