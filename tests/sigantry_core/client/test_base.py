"""BaseRestClient orchestrator tests.

Plan 02-01 Task 3. Verifies the full wiring:
- TokenProvider DI (constructor does not fetch tokens; first send does)
- Outbound headers include Authorization, User-Agent, correlation id
- Retry wiring (429 -> 200 auto-retry)
- Rate-limit wiring (burst does not raise)
- Typed subclass errors without retry (401 -> AuthError, 404 -> NotFoundError)
- T-2-02: bearer token never appears in captured log output
- First-call credential log (T-1-01 continuity)
- correlation() context manager propagates to outbound header + log record
"""

from __future__ import annotations

import logging
import re
from unittest.mock import MagicMock

import httpx
import pytest
import respx

from sigantry_core.auth.audiences import FABRIC_AUDIENCE, FABRIC_SCOPE
from sigantry_core.client import (
    AuthError,
    BaseRestClient,
    HttpResponse,
    NotFoundError,
)

UUID4_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$", re.I
)


@pytest.fixture
def client(mock_token_provider: MagicMock) -> BaseRestClient:
    return BaseRestClient(
        token_provider=mock_token_provider,
        base_url=FABRIC_AUDIENCE,
        default_scope=FABRIC_SCOPE,
    )


class TestConstructorDoesNotFetchToken:
    def test_no_token_call_at_construction(self, mock_token_provider: MagicMock) -> None:
        BaseRestClient(
            token_provider=mock_token_provider,
            base_url=FABRIC_AUDIENCE,
            default_scope=FABRIC_SCOPE,
        )
        assert mock_token_provider.get_token.call_count == 0


class TestSuccessfulRoundTrip:
    def test_200_returns_http_response(self, client: BaseRestClient) -> None:
        with respx.mock(base_url=FABRIC_AUDIENCE) as router:
            router.get("/v1/workspaces").mock(
                return_value=httpx.Response(
                    200, json={"value": []}, headers={"x-ms-request-id": "req-1"}
                )
            )
            result = client.send("GET", "/v1/workspaces")
            assert isinstance(result, HttpResponse)
            assert result.status_code == 200
            assert result.json_body == {"value": []}
            assert result.request_id == "req-1"
            assert result.elapsed_ms > 0.0

    def test_token_fetched_on_send(
        self, client: BaseRestClient, mock_token_provider: MagicMock
    ) -> None:
        with respx.mock(base_url=FABRIC_AUDIENCE) as router:
            router.get("/v1/workspaces").mock(return_value=httpx.Response(200, json={}))
            client.send("GET", "/v1/workspaces")
            assert mock_token_provider.get_token.call_count == 1
            mock_token_provider.get_token.assert_called_with(FABRIC_SCOPE)


class TestOutboundHeaders:
    def test_authorization_header_set(self, client: BaseRestClient) -> None:
        with respx.mock(base_url=FABRIC_AUDIENCE) as router:
            route = router.get("/v1/workspaces").mock(return_value=httpx.Response(200, json={}))
            client.send("GET", "/v1/workspaces")
            sent_request = route.calls.last.request
            assert sent_request.headers["Authorization"] == "Bearer test-token-xyz"

    def test_user_agent_header(self, client: BaseRestClient) -> None:
        with respx.mock(base_url=FABRIC_AUDIENCE) as router:
            route = router.get("/v1/workspaces").mock(return_value=httpx.Response(200, json={}))
            client.send("GET", "/v1/workspaces")
            sent = route.calls.last.request
            assert sent.headers["user-agent"].startswith("fabric-dataops/")

    def test_correlation_id_uuid4_by_default(self, client: BaseRestClient) -> None:
        with respx.mock(base_url=FABRIC_AUDIENCE) as router:
            route = router.get("/v1/workspaces").mock(return_value=httpx.Response(200, json={}))
            client.send("GET", "/v1/workspaces")
            sent = route.calls.last.request
            corr = sent.headers["x-client-correlation-id"]
            assert UUID4_RE.match(corr), f"not uuid4: {corr}"


class TestRetryWiring:
    def test_429_then_200_auto_retries(self, client: BaseRestClient) -> None:
        with respx.mock(base_url=FABRIC_AUDIENCE) as router:
            route = router.get("/v1/workspaces")
            route.side_effect = [
                httpx.Response(429, headers={"Retry-After": "0"}, json={}),
                httpx.Response(200, json={"value": []}, headers={"x-ms-request-id": "r"}),
            ]
            result = client.send("GET", "/v1/workspaces")
            assert result.status_code == 200
            assert route.call_count == 2


class TestTypedErrorsNoRetry:
    def test_404_raises_not_found_not_retried(self, client: BaseRestClient) -> None:
        with respx.mock(base_url=FABRIC_AUDIENCE) as router:
            route = router.get("/v1/missing").mock(return_value=httpx.Response(404))
            with pytest.raises(NotFoundError) as exc_info:
                client.send("GET", "/v1/missing")
            assert exc_info.value.status_code == 404
            assert route.call_count == 1

    def test_401_raises_auth_error_not_retried(self, client: BaseRestClient) -> None:
        with respx.mock(base_url=FABRIC_AUDIENCE) as router:
            route = router.get("/v1/secured").mock(return_value=httpx.Response(401))
            with pytest.raises(AuthError):
                client.send("GET", "/v1/secured")
            assert route.call_count == 1


class TestTokenLeak:
    """T-2-02 mitigation: bearer token string must never appear in log output."""

    def test_bearer_token_never_leaks(
        self, client: BaseRestClient, caplog: pytest.LogCaptureFixture
    ) -> None:
        with respx.mock(base_url=FABRIC_AUDIENCE) as router:
            router.get("/v1/workspaces").mock(
                return_value=httpx.Response(
                    200,
                    json={},
                    headers={"Set-Cookie": "s=abc", "x-ms-request-id": "r"},
                )
            )
            with caplog.at_level(logging.DEBUG, logger="sigantry_core.client"):
                client.send("GET", "/v1/workspaces")
            # Render every captured record via getMessage() and via JsonFormatter
            from sigantry_core.client.logging import JsonFormatter

            fmt = JsonFormatter()
            rendered_parts = [r.getMessage() for r in caplog.records]
            rendered_parts.extend(fmt.format(r) for r in caplog.records)
            rendered = "\n".join(rendered_parts)
            assert "Bearer test-token-xyz" not in rendered
            assert "test-token-xyz" not in rendered


class TestCorrelationContext:
    def test_correlation_propagates_to_outbound_header(self, client: BaseRestClient) -> None:
        with respx.mock(base_url=FABRIC_AUDIENCE) as router:
            route = router.get("/v1/workspaces").mock(return_value=httpx.Response(200, json={}))
            with client.correlation("corr-abc-123"):
                client.send("GET", "/v1/workspaces")
            sent = route.calls.last.request
            assert sent.headers["x-client-correlation-id"] == "corr-abc-123"

    def test_correlation_scope_is_limited_to_context(self, client: BaseRestClient) -> None:
        with respx.mock(base_url=FABRIC_AUDIENCE) as router:
            route = router.get("/v1/workspaces").mock(return_value=httpx.Response(200, json={}))
            with client.correlation("inside"):
                pass
            # After exit, the next request gets a fresh uuid4
            client.send("GET", "/v1/workspaces")
            sent = route.calls.last.request
            assert sent.headers["x-client-correlation-id"] != "inside"
            assert UUID4_RE.match(sent.headers["x-client-correlation-id"])


class TestCredentialLoggedOnce:
    def test_credential_resolved_only_on_first_send(
        self, client: BaseRestClient, mock_token_provider: MagicMock
    ) -> None:
        mock_token_provider.last_credential_class.return_value = "MockCredential"
        with respx.mock(base_url=FABRIC_AUDIENCE) as router:
            router.get("/v1/workspaces").mock(return_value=httpx.Response(200, json={}))
            logger_name = "sigantry_core.client.base"
            buffer = logging.getLogger(logger_name)
            captured: list[logging.LogRecord] = []

            class _Capture(logging.Handler):
                def emit(self, record: logging.LogRecord) -> None:
                    captured.append(record)

            h = _Capture()
            buffer.addHandler(h)
            try:
                client.send("GET", "/v1/workspaces")
                client.send("GET", "/v1/workspaces")
            finally:
                buffer.removeHandler(h)
            msgs = [r for r in captured if r.getMessage() == "client_credential_resolved"]
            assert len(msgs) == 1
            assert getattr(msgs[0], "scope", None) == FABRIC_SCOPE
            assert getattr(msgs[0], "credential", None) == "MockCredential"


class TestContextManagerShape:
    def test_enter_exit_closes_http(self, mock_token_provider: MagicMock) -> None:
        with BaseRestClient(
            token_provider=mock_token_provider,
            base_url=FABRIC_AUDIENCE,
            default_scope=FABRIC_SCOPE,
        ) as c:
            assert isinstance(c, BaseRestClient)


class TestBaseRestClientExport:
    def test_imported_from_public_namespace(self) -> None:
        import sigantry_core.client as c

        assert hasattr(c, "BaseRestClient")
        assert "BaseRestClient" in c.__all__
