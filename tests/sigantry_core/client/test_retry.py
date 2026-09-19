"""Retry policy tests for sigantry_core.client.retry.

Plan 02-01 Task 2. Covers:
- _parse_retry_after: integer seconds, HTTP-date, invalid, None, 60s cap
- _is_retryable_status: only 408/429/500/502/503/504
- End-to-end retry via respx + build_retry_policy()
- classify_response raising the precise subclass for each non-retryable code
"""

from __future__ import annotations

import httpx
import pytest
import respx
import tenacity
from freezegun import freeze_time

from sigantry_core.client.errors import (
    AuthError,
    HttpError,
    NotFoundError,
    RateLimitError,
    ServerError,
)
from sigantry_core.client.retry import (
    IDEMPOTENT_METHODS,
    MAX_ATTEMPTS,
    MAX_WAIT_SECONDS,
    NO_RETRY_STATUSES,
    NON_IDEMPOTENT_RETRY_STATUSES,
    RETRY_STATUSES,
    _is_idempotent,
    _is_retryable_status,
    _parse_retry_after,
    _should_retry_response,
    build_retry_policy,
    classify_response,
    execute_with_retry,
)


class TestParseRetryAfterSeconds:
    def test_integer_seconds(self) -> None:
        assert _parse_retry_after("30") == 30.0

    def test_fractional_seconds(self) -> None:
        assert _parse_retry_after("0.5") == 0.5

    def test_zero(self) -> None:
        assert _parse_retry_after("0") == 0.0

    def test_capped_at_max(self) -> None:
        assert _parse_retry_after("120") == float(MAX_WAIT_SECONDS)
        assert _parse_retry_after("999999") == float(MAX_WAIT_SECONDS)

    def test_negative_clamped_to_zero(self) -> None:
        assert _parse_retry_after("-5") == 0.0


class TestParseRetryAfterHttpDate:
    @freeze_time("2026-10-21 07:27:15 UTC")
    def test_http_date_within_window(self) -> None:
        # A date 45 seconds in the future
        result = _parse_retry_after("Wed, 21 Oct 2026 07:28:00 GMT")
        assert result is not None
        assert 40.0 <= result <= 50.0

    @freeze_time("2026-10-21 07:27:15 UTC")
    def test_http_date_past_clamped_to_zero(self) -> None:
        # A date in the past
        result = _parse_retry_after("Wed, 21 Oct 2026 07:00:00 GMT")
        assert result == 0.0

    @freeze_time("2026-10-21 07:27:15 UTC")
    def test_http_date_far_future_capped(self) -> None:
        result = _parse_retry_after("Wed, 21 Oct 2027 07:28:00 GMT")
        assert result == float(MAX_WAIT_SECONDS)


class TestParseRetryAfterEdgeCases:
    def test_none_returns_none(self) -> None:
        assert _parse_retry_after(None) is None

    def test_garbage_returns_none(self) -> None:
        assert _parse_retry_after("not-a-date") is None

    def test_empty_string_returns_none(self) -> None:
        assert _parse_retry_after("") is None


class TestIsRetryableStatus:
    @pytest.mark.parametrize("code", sorted(RETRY_STATUSES))
    def test_retryable(self, code: int) -> None:
        assert _is_retryable_status(code) is True

    @pytest.mark.parametrize("code", sorted(NO_RETRY_STATUSES))
    def test_non_retryable(self, code: int) -> None:
        assert _is_retryable_status(code) is False

    @pytest.mark.parametrize("code", [200, 201, 202, 204])
    def test_success_not_retryable(self, code: int) -> None:
        assert _is_retryable_status(code) is False


class TestRetryStatusSets:
    def test_retry_set_matches_spec(self) -> None:
        assert frozenset({408, 429, 500, 502, 503, 504}) == RETRY_STATUSES

    def test_no_retry_set_matches_spec(self) -> None:
        assert frozenset({400, 401, 403, 404, 409, 422}) == NO_RETRY_STATUSES

    def test_max_attempts(self) -> None:
        assert MAX_ATTEMPTS == 5

    def test_max_wait_seconds(self) -> None:
        assert MAX_WAIT_SECONDS == 60


class TestRetryLoopEnd2End:
    """End-to-end retry using the callable interface ``policy(callable)``.

    NOTE: tenacity's iterator pattern (``for attempt in policy: with attempt:``)
    does NOT support result-based retry (``retry_if_result``) - only exception
    retry. So BaseRestClient and these tests drive the policy via the
    callable interface instead.
    """

    def test_429_then_200_succeeds(self) -> None:
        with respx.mock(base_url="https://test.example.com") as router:
            route = router.get("/x")
            route.side_effect = [
                httpx.Response(429, headers={"Retry-After": "0"}),
                httpx.Response(200, json={"ok": True}),
            ]
            client = httpx.Client()
            final = execute_with_retry(
                lambda: client.get("https://test.example.com/x"),
                max_attempts=3,
            )
            assert final.status_code == 200
            assert route.call_count == 2

    def test_retried_500_exhausts_returns_last_response(self) -> None:
        # Use Retry-After: 0 so tenacity does not exponential-backoff between calls
        with respx.mock(base_url="https://test.example.com") as router:
            router.get("/x").mock(
                return_value=httpx.Response(500, json={"err": "boom"}, headers={"Retry-After": "0"})
            )
            client = httpx.Client()
            final = execute_with_retry(
                lambda: client.get("https://test.example.com/x"),
                max_attempts=3,
            )
            assert final.status_code == 500
            with pytest.raises(ServerError):
                classify_response(final)

    def test_400_not_retried(self) -> None:
        with respx.mock(base_url="https://test.example.com") as router:
            route = router.get("/x").mock(return_value=httpx.Response(400, json={}))
            client = httpx.Client()
            final = execute_with_retry(
                lambda: client.get("https://test.example.com/x"),
                max_attempts=3,
            )
            assert final.status_code == 400
            assert route.call_count == 1  # no retry
            with pytest.raises(HttpError) as exc_info:
                classify_response(final)
            assert exc_info.value.status_code == 400

    def test_connect_error_retried(self) -> None:
        attempts = {"n": 0}

        def _side_effect(request: httpx.Request) -> httpx.Response:
            attempts["n"] += 1
            if attempts["n"] < 2:
                raise httpx.ConnectError("boom")
            return httpx.Response(200, json={"ok": True})

        with respx.mock(base_url="https://test.example.com") as router:
            router.get("/x").mock(side_effect=_side_effect)
            client = httpx.Client()
            final = execute_with_retry(
                lambda: client.get("https://test.example.com/x"),
                max_attempts=3,
            )
            assert final.status_code == 200
            assert attempts["n"] == 2


class TestClassifyResponse:
    def _resp(self, code: int, body: dict | None = None) -> httpx.Response:
        return httpx.Response(code, json=body or {}, headers={"x-ms-request-id": "r-1"})

    def test_2xx_does_not_raise(self) -> None:
        resp = self._resp(200, {"ok": True})
        # returns None; does not raise
        assert classify_response(resp) is None

    def test_429_raises_rate_limit(self) -> None:
        with pytest.raises(RateLimitError) as exc_info:
            classify_response(self._resp(429, {"err": "throttled"}))
        assert exc_info.value.status_code == 429

    def test_401_raises_auth_error(self) -> None:
        with pytest.raises(AuthError):
            classify_response(self._resp(401))

    def test_403_raises_auth_error(self) -> None:
        with pytest.raises(AuthError):
            classify_response(self._resp(403))

    def test_404_raises_not_found(self) -> None:
        with pytest.raises(NotFoundError):
            classify_response(self._resp(404))

    def test_500_raises_server_error(self) -> None:
        with pytest.raises(ServerError):
            classify_response(self._resp(500))

    def test_503_raises_server_error(self) -> None:
        with pytest.raises(ServerError):
            classify_response(self._resp(503))

    def test_409_raises_http_error(self) -> None:
        # 409 Conflict: not in the subclass whitelist, falls through to HttpError
        with pytest.raises(HttpError) as exc_info:
            classify_response(self._resp(409))
        assert exc_info.value.status_code == 409
        # Should NOT be any of the more-specific subclasses
        assert not isinstance(
            exc_info.value, (AuthError, NotFoundError, ServerError, RateLimitError)
        )

    def test_classify_captures_request_id(self) -> None:
        with pytest.raises(ServerError) as exc_info:
            classify_response(self._resp(500))
        assert exc_info.value.request_id == "r-1"


class TestBuildRetryPolicy:
    def test_returns_tenacity_retrying(self) -> None:
        policy = build_retry_policy()
        assert isinstance(policy, tenacity.Retrying)

    def test_custom_max_attempts(self) -> None:
        policy = build_retry_policy(max_attempts=3)
        # Instantiation succeeds; structural assertion only
        assert isinstance(policy, tenacity.Retrying)

    def test_method_kwarg_accepted(self) -> None:
        # Smoke: every verb plus None instantiates without error.
        for method in (None, "GET", "HEAD", "OPTIONS", "PUT", "POST", "PATCH", "DELETE"):
            policy = build_retry_policy(max_attempts=2, method=method)
            assert isinstance(policy, tenacity.Retrying)


class TestMethodTierClassification:
    """Audit-2026-05-07 W1.3: method-aware retry tier.

    Falsifiability contract: this test class FAILS against the pre-fix
    implementation that retried POST/PATCH/DELETE on every transient 5xx.
    """

    def test_idempotent_methods_set(self) -> None:
        assert frozenset({"GET", "HEAD", "OPTIONS", "PUT"}) == IDEMPOTENT_METHODS

    def test_non_idempotent_retry_statuses_subset(self) -> None:
        # The narrow set used for non-idempotent verbs is a strict subset
        # of the full RETRY_STATUSES; specifically it excludes 500/502/504.
        assert frozenset({408, 429, 503}) == NON_IDEMPOTENT_RETRY_STATUSES
        assert NON_IDEMPOTENT_RETRY_STATUSES <= RETRY_STATUSES
        assert 500 not in NON_IDEMPOTENT_RETRY_STATUSES
        assert 502 not in NON_IDEMPOTENT_RETRY_STATUSES
        assert 504 not in NON_IDEMPOTENT_RETRY_STATUSES

    @pytest.mark.parametrize("method", ["GET", "HEAD", "OPTIONS", "PUT", None])
    def test_idempotent_classifier(self, method: str | None) -> None:
        assert _is_idempotent(method) is True

    @pytest.mark.parametrize("method", ["POST", "PATCH", "DELETE"])
    def test_non_idempotent_classifier(self, method: str) -> None:
        assert _is_idempotent(method) is False

    @pytest.mark.parametrize("method", ["get", "post", "Delete"])
    def test_classifier_is_case_insensitive(self, method: str) -> None:
        # Lowercase / mixed-case verbs must be normalised before lookup;
        # callers passing httpx's lower-case method strings must not
        # silently be miscategorised.
        is_idempotent = method.upper() in IDEMPOTENT_METHODS
        assert _is_idempotent(method) is is_idempotent

    @pytest.mark.parametrize("status", [500, 502, 504])
    def test_non_idempotent_status_500_502_504_NOT_retryable(self, status: int) -> None:  # noqa: N802
        # Without a Retry-After header these statuses do not warrant retry
        # for POST/PATCH/DELETE. The audit identified workspace-create /
        # item-move 503 as the canonical case; 500/502/504 are even more
        # ambiguous (the request may have been applied).
        assert _is_retryable_status(status, "POST") is False
        assert _is_retryable_status(status, "PATCH") is False
        assert _is_retryable_status(status, "DELETE") is False

    @pytest.mark.parametrize("status", [408, 429, 503])
    def test_non_idempotent_narrow_set_status_only_retryable(self, status: int) -> None:
        # _is_retryable_status returns True for the narrow status; the
        # additional Retry-After-header gate is enforced by
        # _should_retry_response.
        assert _is_retryable_status(status, "POST") is True

    @pytest.mark.parametrize("status", sorted(RETRY_STATUSES))
    def test_idempotent_full_set_retryable(self, status: int) -> None:
        assert _is_retryable_status(status, "GET") is True
        assert _is_retryable_status(status, "PUT") is True


class TestShouldRetryResponseMethodAware:
    """Direct unit tests for _should_retry_response with each verb tier."""

    def _resp(self, status: int, *, retry_after: str | None = None) -> httpx.Response:
        headers: dict[str, str] = {}
        if retry_after is not None:
            headers["Retry-After"] = retry_after
        return httpx.Response(status, headers=headers)

    def test_post_503_without_retry_after_NOT_retried(self) -> None:  # noqa: N802
        # The headline case from the audit: workspace create / item move
        # returning 503 on POST must not be retried blindly.
        assert _should_retry_response(self._resp(503), method="POST") is False

    def test_post_503_with_retry_after_IS_retried(self) -> None:  # noqa: N802
        # Server-supplied Retry-After is the only safe signal to resume
        # a non-idempotent verb.
        assert _should_retry_response(self._resp(503, retry_after="1"), method="POST") is True

    def test_post_429_with_retry_after_IS_retried(self) -> None:  # noqa: N802
        assert _should_retry_response(self._resp(429, retry_after="1"), method="POST") is True

    def test_post_429_without_retry_after_NOT_retried(self) -> None:  # noqa: N802
        # Even 429 (rate limit) requires Retry-After for non-idempotent.
        assert _should_retry_response(self._resp(429), method="POST") is False

    @pytest.mark.parametrize("status", [500, 502, 504])
    def test_post_other_5xx_never_retried(self, status: int) -> None:
        # With OR without Retry-After — 500/502/504 fall outside the
        # non-idempotent retry set entirely.
        assert _should_retry_response(self._resp(status), method="POST") is False
        assert _should_retry_response(self._resp(status, retry_after="1"), method="POST") is False

    @pytest.mark.parametrize("status", sorted(RETRY_STATUSES))
    def test_get_full_set_retried_with_or_without_retry_after(self, status: int) -> None:
        # Idempotent verbs ignore the Retry-After-presence check.
        assert _should_retry_response(self._resp(status), method="GET") is True
        assert _should_retry_response(self._resp(status, retry_after="1"), method="GET") is True

    def test_none_method_treated_as_idempotent(self) -> None:
        # Backwards-compat: callers that have not been migrated still see
        # the original full-retry behaviour.
        assert _should_retry_response(self._resp(503), method=None) is True

    def test_none_response_returns_false(self) -> None:
        # Defensive: tenacity's retry_if_result(None) path.
        assert _should_retry_response(None, method="GET") is False
        assert _should_retry_response(None, method="POST") is False

    def test_2xx_never_retried(self) -> None:
        for status in (200, 201, 204):
            assert _should_retry_response(self._resp(status), method="GET") is False
            assert _should_retry_response(self._resp(status), method="POST") is False


class TestExecuteWithRetryMethodAware:
    """End-to-end retry behaviour with method threaded through."""

    def test_post_503_no_retry_after_makes_one_call_only(self) -> None:
        # The audit's primary correctness concern: workspace-create POST
        # 503 must not double-execute.
        with respx.mock(base_url="https://test.example.com") as router:
            route = router.post("/items").mock(
                return_value=httpx.Response(503, json={"err": "transient"}),
            )
            client = httpx.Client()
            final = execute_with_retry(
                lambda: client.post("https://test.example.com/items"),
                max_attempts=5,
                method="POST",
            )
            assert final.status_code == 503
            assert route.call_count == 1  # no retry — non-idempotent without Retry-After

    def test_post_503_with_retry_after_does_retry(self) -> None:
        with respx.mock(base_url="https://test.example.com") as router:
            route = router.post("/items")
            route.side_effect = [
                httpx.Response(503, headers={"Retry-After": "0"}),
                httpx.Response(200, json={"ok": True}),
            ]
            client = httpx.Client()
            final = execute_with_retry(
                lambda: client.post("https://test.example.com/items"),
                max_attempts=3,
                method="POST",
            )
            assert final.status_code == 200
            assert route.call_count == 2

    def test_post_500_makes_one_call_only(self) -> None:
        # Even with a Retry-After hint, 500 is outside the non-idempotent
        # narrow retry set. (Server may have applied the change.)
        with respx.mock(base_url="https://test.example.com") as router:
            route = router.post("/items").mock(
                return_value=httpx.Response(
                    500, json={"err": "boom"}, headers={"Retry-After": "0"}
                ),
            )
            client = httpx.Client()
            final = execute_with_retry(
                lambda: client.post("https://test.example.com/items"),
                max_attempts=5,
                method="POST",
            )
            assert final.status_code == 500
            assert route.call_count == 1

    def test_get_503_retries(self) -> None:
        with respx.mock(base_url="https://test.example.com") as router:
            route = router.get("/x")
            route.side_effect = [
                httpx.Response(503, headers={"Retry-After": "0"}),
                httpx.Response(503, headers={"Retry-After": "0"}),
                httpx.Response(200, json={"ok": True}),
            ]
            client = httpx.Client()
            final = execute_with_retry(
                lambda: client.get("https://test.example.com/x"),
                max_attempts=4,
                method="GET",
            )
            assert final.status_code == 200
            assert route.call_count == 3

    def test_post_connect_error_is_retried(self) -> None:
        # ConnectError = the request never reached the server; safe to
        # retry even for non-idempotent verbs.
        attempts = {"n": 0}

        def _side_effect(_request: httpx.Request) -> httpx.Response:
            attempts["n"] += 1
            if attempts["n"] < 2:
                raise httpx.ConnectError("boom")
            return httpx.Response(200, json={"ok": True})

        with respx.mock(base_url="https://test.example.com") as router:
            router.post("/x").mock(side_effect=_side_effect)
            client = httpx.Client()
            final = execute_with_retry(
                lambda: client.post("https://test.example.com/x"),
                max_attempts=3,
                method="POST",
            )
            assert final.status_code == 200
            assert attempts["n"] == 2

    def test_post_read_timeout_NOT_retried(self) -> None:  # noqa: N802
        # ReadTimeout = the server may have applied the change before the
        # connection idled out. For non-idempotent verbs we must
        # surface this to the caller, not retry.
        attempts = {"n": 0}

        def _side_effect(_request: httpx.Request) -> httpx.Response:
            attempts["n"] += 1
            raise httpx.ReadTimeout("idle")

        with respx.mock(base_url="https://test.example.com") as router:
            router.post("/x").mock(side_effect=_side_effect)
            client = httpx.Client()
            with pytest.raises(httpx.ReadTimeout):
                execute_with_retry(
                    lambda: client.post("https://test.example.com/x"),
                    max_attempts=4,
                    method="POST",
                )
            assert attempts["n"] == 1  # no retry

    def test_get_read_timeout_IS_retried(self) -> None:  # noqa: N802
        # Idempotent ReadTimeout is safe to retry — the worst-case
        # double-execution still yields the same response.
        attempts = {"n": 0}

        def _side_effect(_request: httpx.Request) -> httpx.Response:
            attempts["n"] += 1
            if attempts["n"] < 2:
                raise httpx.ReadTimeout("idle")
            return httpx.Response(200, json={"ok": True})

        with respx.mock(base_url="https://test.example.com") as router:
            router.get("/x").mock(side_effect=_side_effect)
            client = httpx.Client()
            final = execute_with_retry(
                lambda: client.get("https://test.example.com/x"),
                max_attempts=3,
                method="GET",
            )
            assert final.status_code == 200
            assert attempts["n"] == 2

    @pytest.mark.parametrize("verb", ["PATCH", "DELETE"])
    def test_patch_and_delete_behave_like_post(self, verb: str) -> None:
        # PATCH and DELETE share POST's non-idempotent retry tier.
        with respx.mock(base_url="https://test.example.com") as router:
            route = router.route(method=verb, url__startswith="https://test.example.com/x").mock(
                return_value=httpx.Response(503, json={"err": "transient"}),
            )
            client = httpx.Client()
            final = execute_with_retry(
                lambda: client.request(verb, "https://test.example.com/x"),
                max_attempts=5,
                method=verb,
            )
            assert final.status_code == 503
            assert route.call_count == 1  # no retry

    def test_put_behaves_like_get(self) -> None:
        # PUT is idempotent in HTTP semantics; retried on 503.
        with respx.mock(base_url="https://test.example.com") as router:
            route = router.put("/x")
            route.side_effect = [
                httpx.Response(503, headers={"Retry-After": "0"}),
                httpx.Response(200, json={"ok": True}),
            ]
            client = httpx.Client()
            final = execute_with_retry(
                lambda: client.put("https://test.example.com/x"),
                max_attempts=3,
                method="PUT",
            )
            assert final.status_code == 200
            assert route.call_count == 2
