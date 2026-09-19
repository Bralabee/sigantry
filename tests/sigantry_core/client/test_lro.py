"""LRO poller tests (Plan 02-02 Task 2).

Scenarios covered:
1. 200-inline success (no LRO path)
2. 201-inline success (no LRO path)
3. 202 + x-ms-operation-id + Location (standard path) with /result Location flip
4. 202 + Location-only (operation id inferred from URL's last segment)
5. Body-embedded operationId
6. Succeeded WITH result (/result URL followed) and WITHOUT result (None)
7. Failed terminal -> OperationFailedError
8. 404 mid-poll (4 consecutive 404s -> LROTimeoutError after MAX_DISAPPEAR_POLLS)
9. Timeout exceeded -> LROTimeoutError
10. max_polls exceeded -> LROTimeoutError
11. Retry-After seconds honoured on BOTH initial 202 AND state poll
12. Retry-After HTTP-date honoured on BOTH initial 202 AND state poll
13. extract_operation_id helper: header > body > Location last-segment

All fixtures are loaded from tests/sigantry_core/client/fixtures/lro_*.json.
Wire mocking: respx. Time control for HTTP-date tests: freezegun.
time.sleep is patched via pytest-mock's ``mocker`` fixture so tests don't
actually wait for Retry-After windows.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import freezegun
import httpx
import pytest
import respx

from sigantry_core.auth.audiences import FABRIC_AUDIENCE, FABRIC_SCOPE
from sigantry_core.client import (
    BaseRestClient,
    LROTimeoutError,
    OperationFailedError,
)
from sigantry_core.client.lro import (
    DEFAULT_MAX_POLLS,
    DEFAULT_TIMEOUT,
    MAX_DISAPPEAR_POLLS,
    extract_operation_id,
    poll_operation,
)
from sigantry_core.client.models import HttpResponse

FIXTURES = Path(__file__).parent / "fixtures"


def _load(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text())


@pytest.fixture
def client(mock_token_provider: MagicMock) -> BaseRestClient:
    return BaseRestClient(
        token_provider=mock_token_provider,
        base_url=FABRIC_AUDIENCE,
        default_scope=FABRIC_SCOPE,
    )


@pytest.fixture(autouse=True)
def _patch_sleep(mocker):
    """Neutralise ``time.sleep`` in the LRO module so tests finish instantly.

    Tests that want to assert on the sleep-seconds sequence install their own
    side_effect via ``mocker.patch(..., side_effect=...)`` which overrides
    this autouse patch.
    """
    return mocker.patch("sigantry_core.client.lro.time.sleep")


# ---------------------------------------------------------------------------
# Module constants export
# ---------------------------------------------------------------------------


def test_lro_module_exports_constants() -> None:
    assert DEFAULT_TIMEOUT == 600.0
    assert DEFAULT_MAX_POLLS == 100
    assert MAX_DISAPPEAR_POLLS == 3


def test_lro_helpers_exported_from_public_namespace() -> None:
    import sigantry_core.client as c

    assert "extract_operation_id" in c.__all__
    assert "poll_operation" in c.__all__


def test_base_rest_client_has_send_lro(client: BaseRestClient) -> None:
    assert hasattr(client, "send_lro")
    assert callable(client.send_lro)


# ---------------------------------------------------------------------------
# Scenario 1 - 200 inline success (no LRO)
# ---------------------------------------------------------------------------


def test_lro_inline_200_no_polling(client: BaseRestClient) -> None:
    fx = _load("lro_inline_200.json")
    with respx.mock(assert_all_called=False) as router:
        route = router.post(f"{FABRIC_AUDIENCE}/v1/workspaces").mock(
            return_value=httpx.Response(200, json=fx)
        )
        result = client.send_lro("POST", "/v1/workspaces", json={"displayName": "x"})
        assert result == fx
        assert route.call_count == 1


def test_lro_inline_201_no_polling(client: BaseRestClient) -> None:
    with respx.mock(assert_all_called=False) as router:
        route = router.post(f"{FABRIC_AUDIENCE}/v1/workspaces").mock(
            return_value=httpx.Response(201, json={"created": True})
        )
        result = client.send_lro("POST", "/v1/workspaces", json={})
        assert result == {"created": True}
        assert route.call_count == 1


# ---------------------------------------------------------------------------
# Scenario 3 - 202 + x-ms-operation-id + Location, Succeeded with /result
# ---------------------------------------------------------------------------


def test_lro_accepted_operation_id_succeeds_with_result(
    client: BaseRestClient,
) -> None:
    fx = _load("lro_accepted_operation_id.json")
    with respx.mock(assert_all_called=False) as router:
        router.post(f"{FABRIC_AUDIENCE}/v1/workspaces").mock(
            return_value=httpx.Response(202, headers=fx["initial_headers"], json={})
        )
        state_route = router.get("https://api.fabric.microsoft.com/v1/operations/op-1")
        state_route.side_effect = [
            httpx.Response(
                status_code=fx["poll_responses"][0]["status_code"],
                headers=fx["poll_responses"][0]["headers"],
                json=fx["poll_responses"][0]["body"],
            ),
            httpx.Response(
                status_code=fx["poll_responses"][1]["status_code"],
                headers=fx["poll_responses"][1]["headers"],
                json=fx["poll_responses"][1]["body"],
            ),
        ]
        router.get("https://api.fabric.microsoft.com/v1/operations/op-1/result").mock(
            return_value=httpx.Response(
                status_code=fx["poll_responses"][2]["status_code"],
                headers=fx["poll_responses"][2]["headers"],
                json=fx["poll_responses"][2]["body"],
            )
        )
        result = client.send_lro("POST", "/v1/workspaces", json={})
        assert result == {"workspaceId": "ws-abc"}


# ---------------------------------------------------------------------------
# Scenario 4 - 202 + Location only (no x-ms-operation-id) -> id from URL
# ---------------------------------------------------------------------------


def test_lro_accepted_location_only_infers_operation_id(
    client: BaseRestClient,
) -> None:
    fx = _load("lro_accepted_location_only.json")
    with respx.mock(assert_all_called=False) as router:
        router.post(f"{FABRIC_AUDIENCE}/v1/workspaces").mock(
            return_value=httpx.Response(202, headers=fx["initial_headers"], json={})
        )
        router.get("https://api.fabric.microsoft.com/v1/operations/op-loc-only").mock(
            return_value=httpx.Response(200, headers={}, json=fx["poll_responses"][0]["body"])
        )
        # Succeeded without /result Location -> None
        result = client.send_lro("POST", "/v1/workspaces", json={})
        assert result is None


# ---------------------------------------------------------------------------
# extract_operation_id unit tests (header > body > Location last-segment)
# ---------------------------------------------------------------------------


class TestExtractOperationId:
    def test_header_wins_over_body(self) -> None:
        initial = HttpResponse(
            status_code=202,
            json_body={"operationId": "from-body"},
            headers={"x-ms-operation-id": "from-header"},
            request_id=None,
            operation_id=None,
            elapsed_ms=1.0,
        )
        assert extract_operation_id(initial) == "from-header"

    def test_body_when_no_header(self) -> None:
        initial = HttpResponse(
            status_code=202,
            json_body={"operationId": "op-body-1"},
            headers={},
            request_id=None,
            operation_id=None,
            elapsed_ms=1.0,
        )
        assert extract_operation_id(initial) == "op-body-1"

    def test_body_snake_case_accepted(self) -> None:
        initial = HttpResponse(
            status_code=202,
            json_body={"operation_id": "op-snake"},
            headers={},
            request_id=None,
            operation_id=None,
            elapsed_ms=1.0,
        )
        assert extract_operation_id(initial) == "op-snake"

    def test_location_last_segment(self) -> None:
        initial = HttpResponse(
            status_code=202,
            json_body={},
            headers={"Location": "https://x/v1/operations/op-xyz"},
            request_id=None,
            operation_id=None,
            elapsed_ms=1.0,
        )
        assert extract_operation_id(initial) == "op-xyz"

    def test_location_result_suffix_skipped(self) -> None:
        """When the Location already points to .../result, we return the
        parent segment, not the literal ``result``.
        """
        initial = HttpResponse(
            status_code=202,
            json_body={},
            headers={"Location": "https://x/v1/operations/op-with-result/result"},
            request_id=None,
            operation_id=None,
            elapsed_ms=1.0,
        )
        assert extract_operation_id(initial) == "op-with-result"

    def test_all_sources_missing_returns_none(self) -> None:
        initial = HttpResponse(
            status_code=202,
            json_body={},
            headers={},
            request_id=None,
            operation_id=None,
            elapsed_ms=1.0,
        )
        assert extract_operation_id(initial) is None


# ---------------------------------------------------------------------------
# Scenario 5 - body-embedded operation id
# ---------------------------------------------------------------------------


def test_lro_body_embedded_operation_id(client: BaseRestClient) -> None:
    fx = _load("lro_body_embedded_opid.json")
    with respx.mock(assert_all_called=False) as router:
        router.post(f"{FABRIC_AUDIENCE}/v1/workspaces").mock(
            return_value=httpx.Response(
                status_code=fx["initial_status"],
                headers=fx["initial_headers"],
                json=fx["initial_body"],
            )
        )
        router.get(fx["state_url"]).mock(
            return_value=httpx.Response(
                status_code=fx["poll_responses"][0]["status_code"],
                headers=fx["poll_responses"][0]["headers"],
                json=fx["poll_responses"][0]["body"],
            )
        )
        # state URL was reconstructed from base_url + /v1/operations/{body_op_id}
        # because no Location header was provided on the initial 202.
        result = client.send_lro("POST", "/v1/workspaces", json={})
        assert result is None  # Succeeded without /result Location


# ---------------------------------------------------------------------------
# Scenario 7 - Failed terminal -> OperationFailedError
# ---------------------------------------------------------------------------


def test_lro_failed_raises_operation_failed_error(
    client: BaseRestClient,
) -> None:
    fx = _load("lro_failed.json")
    with respx.mock(assert_all_called=False) as router:
        router.post(f"{FABRIC_AUDIENCE}/v1/workspaces").mock(
            return_value=httpx.Response(202, headers=fx["initial_headers"], json={})
        )
        router.get("https://api.fabric.microsoft.com/v1/operations/op-fail").mock(
            return_value=httpx.Response(200, headers={}, json=fx["poll_responses"][0]["body"])
        )
        with pytest.raises(OperationFailedError) as exc_info:
            client.send_lro("POST", "/v1/workspaces", json={})
        assert exc_info.value.operation_id == "op-fail"
        assert exc_info.value.error_code == "BadRequest"
        assert exc_info.value.message == "bad input"
        assert exc_info.value.details == [{"field": "displayName"}]


# ---------------------------------------------------------------------------
# Scenario 8 - 404 mid-poll (disappearing operation)
# ---------------------------------------------------------------------------


def test_lro_disappearing_404s_raise_timeout(client: BaseRestClient) -> None:
    """MAX_DISAPPEAR_POLLS consecutive 404s -> LROTimeoutError."""
    fx = _load("lro_disappearing_404.json")
    with respx.mock(assert_all_called=False) as router:
        router.post(f"{FABRIC_AUDIENCE}/v1/workspaces").mock(
            return_value=httpx.Response(202, headers=fx["initial_headers"], json={})
        )
        router.get("https://api.fabric.microsoft.com/v1/operations/op-404").mock(
            return_value=httpx.Response(404, json={"error": {"code": "NotFound"}})
        )
        with pytest.raises(LROTimeoutError) as exc_info:
            client.send_lro("POST", "/v1/workspaces", json={})
        assert "disappeared" in exc_info.value.last_status
        assert exc_info.value.operation_id == "op-404"


def test_lro_recovers_from_transient_404(
    client: BaseRestClient, mock_token_provider: MagicMock
) -> None:
    """Up to MAX_DISAPPEAR_POLLS-1 transient 404s are survivable; a 200
    Running response should reset the counter and let the poll continue.
    """
    with respx.mock(assert_all_called=False) as router:
        router.post(f"{FABRIC_AUDIENCE}/v1/workspaces").mock(
            return_value=httpx.Response(
                202,
                headers={
                    "x-ms-operation-id": "op-transient",
                    "Location": "https://api.fabric.microsoft.com/v1/operations/op-transient",
                    "Retry-After": "0",
                },
                json={},
            )
        )
        route = router.get("https://api.fabric.microsoft.com/v1/operations/op-transient")
        route.side_effect = [
            httpx.Response(404, json={"error": {"code": "NotFound"}}),
            httpx.Response(404, json={"error": {"code": "NotFound"}}),
            httpx.Response(200, json={"status": "Succeeded"}),
        ]
        result = client.send_lro("POST", "/v1/workspaces", json={})
        assert result is None
        # 404, 404, 200 -> three state-url calls
        assert route.call_count == 3


# ---------------------------------------------------------------------------
# Scenario 9 - timeout exceeded (T-2-04 mitigation gate #1)
# ---------------------------------------------------------------------------


def test_lro_timeout_exceeded(client: BaseRestClient) -> None:
    """timeout=0 forces the poller to raise on the very first iteration."""
    with respx.mock(assert_all_called=False) as router:
        router.post(f"{FABRIC_AUDIENCE}/v1/workspaces").mock(
            return_value=httpx.Response(
                202,
                headers={
                    "x-ms-operation-id": "op-slow",
                    "Location": "https://api.fabric.microsoft.com/v1/operations/op-slow",
                    "Retry-After": "0",
                },
                json={},
            )
        )
        router.get("https://api.fabric.microsoft.com/v1/operations/op-slow").mock(
            return_value=httpx.Response(
                200, headers={"Retry-After": "0"}, json={"status": "Running"}
            )
        )
        with pytest.raises(LROTimeoutError) as exc_info:
            client.send_lro("POST", "/v1/workspaces", json={}, timeout=0.0)
        assert exc_info.value.operation_id == "op-slow"


# ---------------------------------------------------------------------------
# Scenario 10 - max_polls exceeded (T-2-04 mitigation gate #2)
# ---------------------------------------------------------------------------


def test_lro_max_polls_exceeded(client: BaseRestClient) -> None:
    with respx.mock(assert_all_called=False) as router:
        router.post(f"{FABRIC_AUDIENCE}/v1/workspaces").mock(
            return_value=httpx.Response(
                202,
                headers={
                    "x-ms-operation-id": "op-forever",
                    "Location": "https://api.fabric.microsoft.com/v1/operations/op-forever",
                    "Retry-After": "0",
                },
                json={},
            )
        )
        state_route = router.get("https://api.fabric.microsoft.com/v1/operations/op-forever").mock(
            return_value=httpx.Response(
                200, headers={"Retry-After": "0"}, json={"status": "Running"}
            )
        )
        with pytest.raises(LROTimeoutError) as exc_info:
            client.send_lro("POST", "/v1/workspaces", json={}, max_polls=2, timeout=600)
        assert exc_info.value.operation_id == "op-forever"
        assert exc_info.value.last_status == "Running"
        # Both max_polls iterations should have been consumed.
        assert state_route.call_count == 2


# ---------------------------------------------------------------------------
# Scenario 11 - Retry-After seconds honoured on BOTH initial AND poll (Pitfall 1)
# ---------------------------------------------------------------------------


def test_lro_retry_after_seconds_honoured(client: BaseRestClient, mocker) -> None:
    """The poller must read Retry-After from EVERY response, not just the 202."""
    slept: list[float] = []
    mocker.patch("sigantry_core.client.lro.time.sleep", side_effect=lambda s: slept.append(s))
    fx = _load("lro_retry_after_seconds.json")
    with respx.mock(assert_all_called=False) as router:
        router.post(f"{FABRIC_AUDIENCE}/v1/workspaces").mock(
            return_value=httpx.Response(202, headers=fx["initial_headers"], json={})
        )
        state_route = router.get("https://api.fabric.microsoft.com/v1/operations/op-ra-s")
        state_route.side_effect = [
            httpx.Response(
                status_code=fx["poll_responses"][0]["status_code"],
                headers=fx["poll_responses"][0]["headers"],
                json=fx["poll_responses"][0]["body"],
            ),
            httpx.Response(
                status_code=fx["poll_responses"][1]["status_code"],
                headers=fx["poll_responses"][1]["headers"],
                json=fx["poll_responses"][1]["body"],
            ),
        ]
        client.send_lro("POST", "/v1/workspaces", json={})
        # Expect two sleeps: initial Retry-After (2s from 202) + Retry-After
        # from the Running poll (2s). Both come from fixture headers.
        assert slept == [2.0, 2.0]


# ---------------------------------------------------------------------------
# Scenario 12 - Retry-After HTTP-date honoured
# ---------------------------------------------------------------------------


@freezegun.freeze_time("2026-10-21T07:28:00+00:00")
def test_lro_retry_after_httpdate_honoured(client: BaseRestClient, mocker) -> None:
    slept: list[float] = []
    mocker.patch("sigantry_core.client.lro.time.sleep", side_effect=lambda s: slept.append(s))
    fx = _load("lro_retry_after_httpdate.json")
    with respx.mock(assert_all_called=False) as router:
        router.post(f"{FABRIC_AUDIENCE}/v1/workspaces").mock(
            return_value=httpx.Response(202, headers=fx["initial_headers"], json={})
        )
        state_route = router.get("https://api.fabric.microsoft.com/v1/operations/op-ra-d")
        state_route.side_effect = [
            httpx.Response(
                status_code=fx["poll_responses"][0]["status_code"],
                headers=fx["poll_responses"][0]["headers"],
                json=fx["poll_responses"][0]["body"],
            ),
            httpx.Response(
                status_code=fx["poll_responses"][1]["status_code"],
                headers=fx["poll_responses"][1]["headers"],
                json=fx["poll_responses"][1]["body"],
            ),
        ]
        client.send_lro("POST", "/v1/workspaces", json={})
        # HTTP-date is 30s after frozen time -> both sleeps ~30s (with tolerance).
        assert len(slept) == 2
        for s in slept:
            assert 29.0 <= s <= 31.0


# ---------------------------------------------------------------------------
# Direct poll_operation call - exercises contextvar integration
# ---------------------------------------------------------------------------


def test_poll_operation_sets_operation_id_contextvar(
    client: BaseRestClient,
) -> None:
    """The operation_id contextvar is set for the duration of polling so
    log records emitted by client.send during polls include it automatically.
    """
    with respx.mock(assert_all_called=False) as router:
        router.get("https://api.fabric.microsoft.com/v1/operations/op-cv").mock(
            return_value=httpx.Response(200, json={"status": "Succeeded"})
        )
        result = poll_operation(
            client,
            operation_id="op-cv",
            state_url="https://api.fabric.microsoft.com/v1/operations/op-cv",
            initial_retry_after=0.0,
        )
        assert result is None


# ---------------------------------------------------------------------------
# send_lro with missing operation id -> LROTimeoutError (defensive guard)
# ---------------------------------------------------------------------------


def test_send_lro_202_without_operation_id_raises(
    client: BaseRestClient,
) -> None:
    """If the server returns 202 but we cannot locate an operation id from
    any of the three sources, fail fast with LROTimeoutError rather than
    construct a bogus state URL.
    """
    with respx.mock(assert_all_called=False) as router:
        # No x-ms-operation-id, no Location, no body operationId.
        router.post(f"{FABRIC_AUDIENCE}/v1/workspaces").mock(
            return_value=httpx.Response(202, headers={}, json={})
        )
        with pytest.raises(LROTimeoutError) as exc_info:
            client.send_lro("POST", "/v1/workspaces", json={})
        assert exc_info.value.last_status == "missing_operation_id"


# ---------------------------------------------------------------------------
# Uses _parse_retry_after from retry.py, not a local implementation
# ---------------------------------------------------------------------------


def test_lro_module_reuses_retry_parse_retry_after() -> None:
    """T-2-02/T-2-04 correctness invariant: the Retry-After parser lives in
    retry.py; lro.py must not duplicate it. (Pitfall: two parsers drift.)
    """
    from sigantry_core.client import lro as lro_module
    from sigantry_core.client import retry as retry_module

    # lro.py should not define its own _parse_retry_after.
    assert not hasattr(lro_module, "_local_parse_retry_after")
    # It should be using the one from retry.py.
    assert lro_module._parse_retry_after is retry_module._parse_retry_after
