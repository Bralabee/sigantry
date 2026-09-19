"""Per-endpoint rate-limit enforcement via pyrate-limiter Leaky Bucket.

CLIENT-05:
- Each ``(method, path_pattern)`` entry in
  :data:`sigantry_core.client.rate_limits.RATE_LIMITS` gets its own
  ``pyrate_limiter.Limiter`` lazily constructed on first use.
- ``find_bucket_key`` scores candidate patterns so a specific entry beats
  the catch-all ``("*", "*")``.
- ``try_acquire`` blocks until the bucket can accept the request
  (``blocking=True``) rather than raising - client-side throttling is meant
  to pace, not fail.
- ``reset_buckets`` clears the in-memory registry; conftest uses it as an
  autouse fixture to prevent state leakage between tests (Pitfall 5).

Upstream-API note: pyrate-limiter 4.1 ``Limiter.try_acquire`` signature is
``(name, weight=1, blocking=True, timeout=-1)`` where ``timeout=-1`` means
"wait indefinitely". We pass ``blocking=True`` explicitly for clarity.
"""

from __future__ import annotations

import fnmatch
import logging
import re
import threading

from pyrate_limiter import Duration, Limiter, Rate

from sigantry_core.client.rate_limits import RATE_LIMITS

logger = logging.getLogger("sigantry_core.client.rate_limit")

_BUCKETS_LOCK = threading.RLock()
_BUCKETS: dict[tuple[str, str], Limiter] = {}


def _compile_pattern(pattern: str) -> str:
    """Convert ``/v1/workspaces/{id}/items`` into an ``fnmatch`` glob.

    ``{placeholder}`` becomes ``*`` (single-segment wildcard) so fnmatch can
    resolve a path like ``/v1/workspaces/abc/items`` against it.
    """
    return re.sub(r"\{[^/]+\}", "*", pattern)


def _method_matches(bucket_method: str, actual_method: str) -> bool:
    return bucket_method == "*" or bucket_method.upper() == actual_method.upper()


def find_bucket_key(method: str, path: str) -> tuple[str, str]:
    """Return the most-specific ``(method, path_pattern)`` tuple matching ``(method, path)``.

    Specificity rule: exact method matches score higher than the ``*``
    wildcard, and within the same method tier the longer pattern wins. Falls
    back to ``("*", "*")`` if nothing else matches.
    """
    best: tuple[str, str] | None = None
    best_score = -1
    for bm, bp in RATE_LIMITS:
        if not _method_matches(bm, method):
            continue
        glob = _compile_pattern(bp) if "{" in bp else bp
        if fnmatch.fnmatchcase(path, glob):
            # exact-method scores 10_000; catch-all method scores 0;
            # pattern length is the tiebreaker.
            method_score = 0 if bm == "*" else 10_000
            score = method_score + len(bp)
            if score > best_score:
                best = (bm, bp)
                best_score = score
    if best is None:
        return ("*", "*")
    return best


def _get_limiter(key: tuple[str, str]) -> Limiter:
    with _BUCKETS_LOCK:
        lim = _BUCKETS.get(key)
        if lim is None:
            max_calls, period_seconds = RATE_LIMITS[key]
            # pyrate_limiter 4.1: Duration.SECOND == 1000ms, so period_seconds *
            # Duration.SECOND gives the interval in milliseconds.
            rate = Rate(max_calls, Duration.SECOND * period_seconds)
            lim = Limiter(rate)
            _BUCKETS[key] = lim
        return lim


def try_acquire(method: str, path: str, *, weight: int = 1) -> None:
    """Block until the matching bucket can accept the call.

    Never raises on bucket starvation - pyrate-limiter's ``blocking=True``
    handles the wait. This means the caller cannot tell by return-value
    whether the bucket was saturated, which is by design: rate-limiting at
    this layer is pacing, not 429-prevention-or-abort.
    """
    key = find_bucket_key(method, path)
    limiter = _get_limiter(key)
    bucket_name = f"{key[0]} {key[1]}"
    limiter.try_acquire(bucket_name, weight=weight, blocking=True)


def reset_buckets() -> None:
    """Clear all buckets (test-only fixture support - Pitfall 5)."""
    with _BUCKETS_LOCK:
        _BUCKETS.clear()
