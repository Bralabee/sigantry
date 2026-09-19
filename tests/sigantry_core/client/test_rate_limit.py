"""Per-endpoint rate-limit registry tests for sigantry_core.client.rate_limit.

Plan 02-01 Task 2. Validates:
- RATE_LIMITS has the required specific entries + catch-all
- find_bucket_key returns the most-specific pattern match
- try_acquire never raises on burst (client-side throttling, not 429)
- reset_buckets clears in-memory state
- Thread safety: 8 workers x 30 calls against a (100, 60) bucket
"""

from __future__ import annotations

import concurrent.futures
import time

import pyrate_limiter

from sigantry_core.client.rate_limit import (
    find_bucket_key,
    reset_buckets,
    try_acquire,
)
from sigantry_core.client.rate_limits import RATE_LIMITS


class TestRateLimitsRegistry:
    def test_workspaces_list_present(self) -> None:
        assert RATE_LIMITS[("GET", "/v1/workspaces")] == (200, 60)

    def test_workspaces_create_present(self) -> None:
        assert ("POST", "/v1/workspaces") in RATE_LIMITS

    def test_workspace_items_list_present(self) -> None:
        assert RATE_LIMITS[("GET", "/v1/workspaces/{id}/items")] == (100, 60)

    def test_admin_hour_scoped(self) -> None:
        assert ("GET", "/v1/admin/*") in RATE_LIMITS
        _calls, period = RATE_LIMITS[("GET", "/v1/admin/*")]
        assert period == 3600

    def test_catch_all_present(self) -> None:
        assert ("*", "*") in RATE_LIMITS
        assert RATE_LIMITS[("*", "*")][0] >= 100

    def test_at_least_six_specific_entries(self) -> None:
        specific = [(m, p) for (m, p) in RATE_LIMITS if m != "*" and p != "*"]
        assert len(specific) >= 6


class TestFindBucketKey:
    def test_exact_match_wins(self) -> None:
        assert find_bucket_key("GET", "/v1/workspaces") == ("GET", "/v1/workspaces")

    def test_placeholder_match(self) -> None:
        assert find_bucket_key("GET", "/v1/workspaces/abc123/items") == (
            "GET",
            "/v1/workspaces/{id}/items",
        )

    def test_fallback_to_catch_all(self) -> None:
        assert find_bucket_key("PATCH", "/v1/fake") == ("*", "*")

    def test_admin_glob(self) -> None:
        # The legacy "/v1/admin/tenantSettings" (camelCase) is not a real Fabric
        # endpoint; the real endpoint is the lowercase ``/v1/admin/tenantsettings``
        # added in Plan 03-04. This test uses an uncatalogued admin sub-path so
        # the ``/v1/admin/*`` wildcard remains the most-specific match.
        assert find_bucket_key("GET", "/v1/admin/unknown-subpath") == (
            "GET",
            "/v1/admin/*",
        )

    def test_admin_tenantsettings_specific_bucket_wins_over_wildcard(self) -> None:
        """Plan 03-04 W-5 / Pitfall 8 regression.

        ``/v1/admin/tenantsettings`` is capped at 25 req/minute (60% = 15/min)
        and MUST resolve to that specific bucket at runtime. The ``/v1/admin/*``
        wildcard bucket is hour-scoped (200/hr) — silently falling back to it
        would exceed the per-minute cap by ~13x on burst and trigger 429s with
        no backoff budget. Regression-guards the ordering of specific vs
        wildcard entries in ``RATE_LIMITS``.
        """
        key = find_bucket_key("GET", "/v1/admin/tenantsettings")
        assert key == ("GET", "/v1/admin/tenantsettings")
        assert RATE_LIMITS[key] == (15, 60)

    def test_method_mismatch_falls_to_catch_all(self) -> None:
        # /v1/workspaces is defined for GET and POST - a DELETE must fall through
        result = find_bucket_key("DELETE", "/v1/workspaces")
        assert result == ("*", "*")

    def test_specific_beats_wildcard(self) -> None:
        # Any specific route must beat the catch-all
        key = find_bucket_key("GET", "/v1/workspaces")
        assert key != ("*", "*")


class TestTryAcquireBasic:
    def test_does_not_raise_on_normal_usage(self) -> None:
        try_acquire("GET", "/v1/workspaces")  # one call - well under limit

    def test_catch_all_path(self) -> None:
        try_acquire("PATCH", "/v1/anything-uncatalogued")


class TestResetBuckets:
    def test_reset_clears_state(self) -> None:
        try_acquire("GET", "/v1/workspaces")
        reset_buckets()
        # After reset, a fresh call must still succeed
        try_acquire("GET", "/v1/workspaces")


class TestThreadSafety:
    """T-2-03 mitigation smoke: bucket must handle concurrent callers.

    Uses the catch-all bucket (``("*", "*")``) which is 1000 per 60s - that
    headroom lets 240 concurrent calls complete in well under a second while
    still stressing the pyrate-limiter lock path.
    """

    def test_8_workers_30_calls_catchall(self) -> None:
        reset_buckets()
        errors: list[BaseException] = []

        def _worker() -> int:
            local = 0
            try:
                for _ in range(30):
                    # Catch-all path (1000/60s): bucket has enough headroom
                    # for a 240-call burst without engaging the leaky-bucket wait.
                    try_acquire("PATCH", "/v1/uncatalogued-test-path")
                    local += 1
            except BaseException as exc:
                errors.append(exc)
            return local

        started = time.monotonic()
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(lambda _: _worker(), range(8)))
        elapsed = time.monotonic() - started

        total_local = sum(results)
        assert not errors, f"workers raised: {errors!r}"
        assert total_local == 240, f"expected 240 total calls, got {total_local}"
        # Should be fast - the (1000, 60) catch-all has plenty of headroom
        assert elapsed < 30.0, f"took too long: {elapsed:.1f}s"


class TestPyrateLimiterVersionGuard:
    def test_version_is_4x(self) -> None:
        assert pyrate_limiter.__version__.startswith("4."), (
            f"rate_limit.py targets pyrate_limiter 4.x, got {pyrate_limiter.__version__}"
        )
