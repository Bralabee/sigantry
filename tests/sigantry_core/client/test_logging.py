"""Structured-logging tests for sigantry_core.client.logging.

Plan 02-01 Task 1. Covers:
- contextvar correlation id set/get/reset
- JsonFormatter emits the canonical field set
- Authorization / X-Api-Key / Set-Cookie values redacted
- Body logging off at INFO, truncated at 2048 bytes at DEBUG
- Token substring never leaks into formatted output
"""

from __future__ import annotations

import json
import logging

import pytest

from sigantry_core.client.logging import (
    BODY_LOG_MAX_BYTES,
    SENSITIVE_HEADERS,
    JsonFormatter,
    configure_client_logging,
    get_correlation_id,
    get_operation_id,
    reset_correlation_id,
    reset_operation_id,
    set_correlation_id,
    set_operation_id,
)


@pytest.fixture
def formatter() -> JsonFormatter:
    return JsonFormatter()


def _make_record(
    msg: str = "test",
    extra: dict | None = None,
    level: int = logging.INFO,
) -> logging.LogRecord:
    logger = logging.getLogger("sigantry_core.client.test")
    record = logger.makeRecord(
        name="sigantry_core.client.test",
        level=level,
        fn=__file__,
        lno=1,
        msg=msg,
        args=(),
        exc_info=None,
    )
    if extra:
        for k, v in extra.items():
            setattr(record, k, v)
    return record


class TestCorrelationContextVar:
    def test_default_is_none(self) -> None:
        assert get_correlation_id() is None

    def test_set_and_get(self) -> None:
        token = set_correlation_id("abc-123")
        try:
            assert get_correlation_id() == "abc-123"
        finally:
            reset_correlation_id(token)

    def test_reset_restores_previous(self) -> None:
        t1 = set_correlation_id("first")
        t2 = set_correlation_id("second")
        assert get_correlation_id() == "second"
        reset_correlation_id(t2)
        assert get_correlation_id() == "first"
        reset_correlation_id(t1)
        assert get_correlation_id() is None

    def test_operation_id_contextvar(self) -> None:
        assert get_operation_id() is None
        tok = set_operation_id("op-xyz")
        try:
            assert get_operation_id() == "op-xyz"
        finally:
            reset_operation_id(tok)
        assert get_operation_id() is None


class TestJsonFormatterFields:
    def test_canonical_fields_present(self, formatter: JsonFormatter) -> None:
        record = _make_record("hi")
        out = formatter.format(record)
        payload = json.loads(out)
        for key in (
            "ts",
            "level",
            "logger",
            "message",
            "correlation_id",
            "operation_id",
            "method",
            "url",
            "status",
            "elapsed_ms",
            "retry_count",
            "request_id",
        ):
            assert key in payload, f"missing key: {key}"

    def test_missing_fields_default_to_null(self, formatter: JsonFormatter) -> None:
        record = _make_record("hi")
        payload = json.loads(formatter.format(record))
        assert payload["method"] is None
        assert payload["url"] is None
        assert payload["status"] is None
        assert payload["retry_count"] is None
        assert payload["request_id"] is None

    def test_correlation_id_captured_from_contextvar(self, formatter: JsonFormatter) -> None:
        tok = set_correlation_id("corr-abc")
        try:
            record = _make_record("hi")
            payload = json.loads(formatter.format(record))
            assert payload["correlation_id"] == "corr-abc"
        finally:
            reset_correlation_id(tok)

    def test_extras_flow_through(self, formatter: JsonFormatter) -> None:
        record = _make_record(
            "req",
            extra={"method": "GET", "url": "https://x", "status": 200, "retry_count": 1},
        )
        payload = json.loads(formatter.format(record))
        assert payload["method"] == "GET"
        assert payload["url"] == "https://x"
        assert payload["status"] == 200
        assert payload["retry_count"] == 1


class TestHeaderRedaction:
    def test_authorization_redacted(self, formatter: JsonFormatter) -> None:
        record = _make_record(
            "req",
            extra={"headers": {"Authorization": "Bearer eyJ-secret-xyz"}},
        )
        out = formatter.format(record)
        payload = json.loads(out)
        assert payload["headers"]["Authorization"] == "<redacted>"
        assert "Bearer eyJ-secret-xyz" not in out
        assert "eyJ-secret-xyz" not in out

    def test_x_api_key_redacted(self, formatter: JsonFormatter) -> None:
        record = _make_record("req", extra={"headers": {"X-Api-Key": "k-secret-9999"}})
        out = formatter.format(record)
        payload = json.loads(out)
        assert payload["headers"]["X-Api-Key"] == "<redacted>"
        assert "k-secret-9999" not in out

    def test_set_cookie_redacted(self, formatter: JsonFormatter) -> None:
        record = _make_record("req", extra={"headers": {"Set-Cookie": "session=very-secret"}})
        out = formatter.format(record)
        assert "very-secret" not in out

    def test_case_insensitive_redaction(self, formatter: JsonFormatter) -> None:
        record = _make_record("req", extra={"headers": {"authorization": "Bearer leaky-xyz"}})
        out = formatter.format(record)
        assert "leaky-xyz" not in out

    def test_non_sensitive_header_passthrough(self, formatter: JsonFormatter) -> None:
        record = _make_record("req", extra={"headers": {"Content-Type": "application/json"}})
        payload = json.loads(formatter.format(record))
        assert payload["headers"]["Content-Type"] == "application/json"


class TestBodyLogging:
    def test_body_absent_when_not_provided(self, formatter: JsonFormatter) -> None:
        record = _make_record("req")
        payload = json.loads(formatter.format(record))
        assert "body" not in payload

    def test_body_truncated_at_max_bytes(self, formatter: JsonFormatter) -> None:
        big = "x" * (BODY_LOG_MAX_BYTES + 500)
        record = _make_record("req", extra={"body": big}, level=logging.DEBUG)
        payload = json.loads(formatter.format(record))
        assert "body" in payload
        # The truncated field should be <= 2048 bytes of payload + suffix marker
        assert len(payload["body"]) <= BODY_LOG_MAX_BYTES + len("...[truncated]")
        assert payload["body"].endswith("...[truncated]")

    def test_body_max_bytes_constant(self) -> None:
        assert BODY_LOG_MAX_BYTES == 2048


class TestSensitiveHeadersSet:
    def test_denylist_contents(self) -> None:
        assert "authorization" in SENSITIVE_HEADERS
        assert "x-api-key" in SENSITIVE_HEADERS
        assert "set-cookie" in SENSITIVE_HEADERS
        assert "cookie" in SENSITIVE_HEADERS


class TestConfigureClientLogging:
    def test_idempotent(self) -> None:
        """Calling configure_client_logging() twice installs only one handler."""
        logger = logging.getLogger("sigantry_core.client")
        # Strip any existing state for this test
        for h in list(logger.handlers):
            logger.removeHandler(h)
        configure_client_logging()
        initial_count = sum(
            1
            for h in logger.handlers
            if isinstance(h, logging.StreamHandler) and isinstance(h.formatter, JsonFormatter)
        )
        configure_client_logging()
        after_count = sum(
            1
            for h in logger.handlers
            if isinstance(h, logging.StreamHandler) and isinstance(h.formatter, JsonFormatter)
        )
        assert initial_count == 1
        assert after_count == 1


class TestTokenLeakAcrossRecords:
    def test_parallel_correlation_ids_isolated(self, formatter: JsonFormatter) -> None:
        """Ensure contextvar.copy_context prevents cross-record leakage."""
        import contextvars

        captured: list[str] = []

        def _run(cid: str) -> None:
            tok = set_correlation_id(cid)
            try:
                record = _make_record("x")
                captured.append(json.loads(formatter.format(record))["correlation_id"])
            finally:
                reset_correlation_id(tok)

        ctx_a = contextvars.copy_context()
        ctx_b = contextvars.copy_context()
        ctx_a.run(_run, "aaa")
        ctx_b.run(_run, "bbb")
        assert captured == ["aaa", "bbb"]
