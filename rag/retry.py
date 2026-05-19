"""Generic retry-with-exponential-backoff helper.

Used by `rag.embedder` (httpx) and `scripts/openalex_download.py` (requests).
The HTTP client is the caller's choice; pass the exception class to catch via
the `exc` parameter so this module stays library-agnostic.
"""

import time
from collections.abc import Callable
from typing import TypeVar

T = TypeVar("T")

MAX_ATTEMPTS = 3
BACKOFF_BASE = 2.0


def with_retry(fn: Callable[[], T], exc: type[BaseException]) -> T:
    """Call `fn()` with exponential backoff, retrying up to `MAX_ATTEMPTS` times.

    Sleeps `BACKOFF_BASE ** attempt` seconds between attempts (no sleep before
    the first). Re-raises the last `exc` after the final attempt.

    Args:
        fn: Zero-arg callable that performs the request and returns the result.
        exc: Exception class to catch. Anything else propagates immediately.
            Use `httpx.HTTPError` for httpx callers, `requests.RequestException`
            for requests callers.

    Returns:
        Whatever `fn()` returns on the first successful attempt.

    Raises:
        exc: After `MAX_ATTEMPTS` failed attempts.
    """
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
