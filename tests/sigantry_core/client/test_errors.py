"""Error hierarchy tests for sigantry_core.client.errors.

Plan 02-01 Task 1. Asserts the shape documented in the plan's <behavior> block
and the RESEARCH.md §14.1 error hierarchy.
"""

from __future__ import annotations

import pytest

from sigantry_core.client.errors import (
    AuthError,
    ClientError,
    HttpError,
    LROTimeoutError,
    NotFoundError,
    OperationFailedError,
    PaginationError,
    RateLimitError,
    ServerError,
)


class TestClientErrorBase:
    def test_client_error_is_exception(self) -> None:
        assert issubclass(ClientError, Exception)

    def test_http_error_is_client_error(self) -> None:
        assert issubclass(HttpError, ClientError)


class TestHttpErrorFormatting:
    def test_str_format(self) -> None:
        err = HttpError(status_code=429, body={}, request_id="abc", operation_id=None)
        assert str(err) == "HTTP 429 request_id='abc'"

    def test_status_code_attribute(self) -> None:
        err = HttpError(status_code=500, body=None, request_id=None)
        assert err.status_code == 500

    def test_request_id_attribute(self) -> None:
        err = HttpError(status_code=404, body=None, request_id="req-xyz")
        assert err.request_id == "req-xyz"

    def test_operation_id_attribute(self) -> None:
        err = HttpError(status_code=202, body=None, request_id=None, operation_id="op-1")
        assert err.operation_id == "op-1"


class TestHttpErrorSubclasses:
    def test_rate_limit_error_subclass(self) -> None:
        assert issubclass(RateLimitError, HttpError)
        err = RateLimitError(status_code=429, body=None, request_id="r")
        assert err.status_code == 429

    def test_auth_error_subclass(self) -> None:
        assert issubclass(AuthError, HttpError)
        err = AuthError(status_code=401, body=None, request_id=None)
        assert err.status_code == 401

    def test_not_found_error_subclass(self) -> None:
        assert issubclass(NotFoundError, HttpError)
        err = NotFoundError(status_code=404, body=None, request_id=None)
        assert err.status_code == 404

    def test_server_error_subclass(self) -> None:
        assert issubclass(ServerError, HttpError)
        err = ServerError(status_code=503, body=None, request_id=None)
        assert err.status_code == 503


class TestLROAndOperationErrors:
    def test_lro_timeout_not_http_error(self) -> None:
        # LROTimeoutError is ClientError but NOT HttpError (no HTTP status involved)
        assert issubclass(LROTimeoutError, ClientError)
        assert not issubclass(LROTimeoutError, HttpError)

    def test_lro_timeout_fields(self) -> None:
        err = LROTimeoutError(operation_id="op-1", elapsed_seconds=601.0, last_status="Running")
        assert err.operation_id == "op-1"
        assert err.elapsed_seconds == 601.0
        assert err.last_status == "Running"

    def test_operation_failed_not_http_error(self) -> None:
        assert issubclass(OperationFailedError, ClientError)
        assert not issubclass(OperationFailedError, HttpError)

    def test_operation_failed_fields(self) -> None:
        err = OperationFailedError(
            operation_id="op-1",
            error_code="BadRequest",
            message="workspace already exists",
            details=None,
        )
        assert err.operation_id == "op-1"
        assert err.error_code == "BadRequest"
        assert err.message == "workspace already exists"
        assert err.details is None

    def test_operation_failed_details_list(self) -> None:
        err = OperationFailedError(
            operation_id="op-2",
            error_code="Conflict",
            message="x",
            details=[{"code": "A", "msg": "b"}],
        )
        assert err.details == [{"code": "A", "msg": "b"}]

    def test_pagination_error_not_http_error(self) -> None:
        assert issubclass(PaginationError, ClientError)
        assert not issubclass(PaginationError, HttpError)


class TestRaiseable:
    def test_http_error_raises(self) -> None:
        with pytest.raises(HttpError):
            raise HttpError(status_code=500, body=None, request_id=None)

    def test_lro_timeout_raises(self) -> None:
        with pytest.raises(LROTimeoutError):
            raise LROTimeoutError(operation_id="op", elapsed_seconds=1.0, last_status="Running")
