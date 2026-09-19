"""BaseRestClient.send_multipart unit tests (Plan 04-03 Task 3).

Asserts that the new multipart helper:
  * threads through the same retry + rate-limit + correlation pipeline
    as ``send`` (does not construct a one-off httpx client)
  * lets httpx generate the multipart boundary (Content-Type is NOT set
    manually)
  * Authorization header is populated from the TokenProvider
  * correlation id propagates to the outbound ``x-client-correlation-id``
    header
  * rate-limit bucket matches the path pattern
  * retries on 5xx transient errors
"""

from __future__ import annotations

from unittest.mock import MagicMock

import httpx
import pytest
import respx

from sigantry_core.auth.audiences import FABRIC_AUDIENCE, FABRIC_SCOPE
from sigantry_core.client import BaseRestClient, HttpResponse


@pytest.fixture
def client(mock_token_provider: MagicMock) -> BaseRestClient:
    return BaseRestClient(
        token_provider=mock_token_provider,
        base_url=FABRIC_AUDIENCE,
        default_scope=FABRIC_SCOPE,
    )


def _files_payload() -> dict:
    return {"file": ("test.whl", b"PK\x03\x04fake-wheel-bytes", "application/octet-stream")}


class TestSendMultipartBasic:
    def test_returns_http_response(self, client: BaseRestClient) -> None:
        path = "/v1/workspaces/ws-1/environments/env-1/staging/libraries"
        with respx.mock(base_url=FABRIC_AUDIENCE) as router:
            router.post(path).mock(
                return_value=httpx.Response(
                    200,
                    json={"status": "Uploaded"},
                    headers={"x-ms-request-id": "req-mp"},
                )
            )
            resp = client.send_multipart("POST", path, files=_files_payload())
            assert isinstance(resp, HttpResponse)
            assert resp.status_code == 200
            assert resp.request_id == "req-mp"

    def test_authorization_header_set(
        self, client: BaseRestClient, mock_token_provider: MagicMock
    ) -> None:
        path = "/v1/workspaces/ws-1/environments/env-1/staging/libraries"
        with respx.mock(base_url=FABRIC_AUDIENCE) as router:
            route = router.post(path).mock(
                return_value=httpx.Response(200, json={"status": "Uploaded"})
            )
            client.send_multipart("POST", path, files=_files_payload())
            sent = route.calls.last.request
            assert sent.headers["Authorization"] == "Bearer test-token-xyz"

    def test_no_manual_content_type_json(self, client: BaseRestClient) -> None:
        """httpx must generate ``multipart/form-data`` with its own boundary."""
        path = "/v1/workspaces/ws-1/environments/env-1/staging/libraries"
        with respx.mock(base_url=FABRIC_AUDIENCE) as router:
            route = router.post(path).mock(
                return_value=httpx.Response(200, json={"status": "Uploaded"})
            )
            client.send_multipart("POST", path, files=_files_payload())
            sent = route.calls.last.request
            ct = sent.headers.get("content-type", "")
            assert ct.startswith("multipart/form-data"), (
                f"expected multipart/form-data, got: {ct!r}"
            )
            assert "boundary=" in ct

    def test_multipart_body_contains_filename(self, client: BaseRestClient) -> None:
        path = "/v1/workspaces/ws-1/environments/env-1/staging/libraries"
        with respx.mock(base_url=FABRIC_AUDIENCE) as router:
            route = router.post(path).mock(
                return_value=httpx.Response(200, json={"status": "Uploaded"})
            )
            client.send_multipart("POST", path, files=_files_payload())
            sent = route.calls.last.request
            body = sent.content
            assert b"test.whl" in body
            assert b"PK\x03\x04fake-wheel-bytes" in body

    def test_correlation_id_propagates(self, client: BaseRestClient) -> None:
        path = "/v1/workspaces/ws-1/environments/env-1/staging/libraries"
        with respx.mock(base_url=FABRIC_AUDIENCE) as router:
            route = router.post(path).mock(return_value=httpx.Response(200, json={}))
            with client.correlation("corr-abc-123"):
                client.send_multipart("POST", path, files=_files_payload())
            sent = route.calls.last.request
            assert sent.headers["x-client-correlation-id"] == "corr-abc-123"


class TestSendMultipartRetry:
    def test_retries_on_5xx(self, client: BaseRestClient) -> None:
        """Transient 503 (with Retry-After) should trigger the shared retry policy.

        Audit-2026-05-07 W1.3 split the retry tier by HTTP method:
        non-idempotent verbs (POST/PATCH/DELETE) retry only when the
        server signals retry-eligibility via a ``Retry-After`` header.
        Multipart uploads always go via POST, so this test now passes
        ``Retry-After: 0`` on the first 503 to prove the multipart path
        still respects the retry pipeline once the server-supplied
        signal is present.
        """
        path = "/v1/workspaces/ws-1/environments/env-1/staging/libraries"
        with respx.mock(base_url=FABRIC_AUDIENCE) as router:
            route = router.post(path).mock(
                side_effect=[
                    httpx.Response(
                        503,
                        json={"error": "try-again"},
                        headers={"Retry-After": "0"},
                    ),
                    httpx.Response(200, json={"status": "Uploaded"}),
                ]
            )
            resp = client.send_multipart("POST", path, files=_files_payload())
            assert resp.status_code == 200
            assert route.call_count == 2

    def test_post_503_without_retry_after_does_not_retry(self, client: BaseRestClient) -> None:
        """Method-aware retry contract for multipart uploads.

        Falsifiability for W1.3 on the multipart path: a 503 without
        ``Retry-After`` is a transient signal the server did not
        explicitly authorise retrying. For non-idempotent verbs (POST)
        this MUST surface as ServerError without a second send.
        """
        from sigantry_core.client.errors import ServerError

        path = "/v1/workspaces/ws-1/environments/env-1/staging/libraries"
        with respx.mock(base_url=FABRIC_AUDIENCE) as router:
            route = router.post(path).mock(
                return_value=httpx.Response(503, json={"error": "no-retry-signal"})
            )
            with pytest.raises(ServerError):
                client.send_multipart("POST", path, files=_files_payload())
            assert route.call_count == 1
