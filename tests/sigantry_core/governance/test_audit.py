"""Unit tests for sigantry_core.governance.audit (WKSP-06)."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from sigantry_core.client.logging import reset_correlation_id, set_correlation_id
from sigantry_core.governance import DestructiveOpError, destructive_op


def _make_decorated(resource_kind: str, action: str):
    spy = MagicMock(return_value="ran")

    @destructive_op(resource_kind, action)
    def op(*args, **kwargs):
        spy(*args, **kwargs)
        return "ran"

    return op, spy


def test_public_exports() -> None:
    import sigantry_core.governance as g

    assert "destructive_op" in g.__all__
    assert "DestructiveOpError" in g.__all__


def test_destructive_op_error_is_runtime_error() -> None:
    assert issubclass(DestructiveOpError, RuntimeError)


def test_rejects_when_force_missing() -> None:
    op, spy = _make_decorated("workspace", "delete")
    with pytest.raises(DestructiveOpError, match="force=True"):
        op(resource_id="ws-1")
    spy.assert_not_called()


def test_rejects_when_force_false() -> None:
    op, spy = _make_decorated("workspace", "delete")
    with pytest.raises(DestructiveOpError, match="force=True"):
        op(resource_id="ws-1", force=False)
    spy.assert_not_called()


def test_capacity_pause_requires_runbook(mock_token_provider: MagicMock) -> None:
    op, spy = _make_decorated("capacity", "pause")
    with pytest.raises(DestructiveOpError, match="runbook_id"):
        op(resource_id="cap-1", force=True, token_provider=mock_token_provider)
    spy.assert_not_called()


def test_capacity_resume_requires_runbook(mock_token_provider: MagicMock) -> None:
    op, spy = _make_decorated("capacity", "resume")
    with pytest.raises(DestructiveOpError, match="runbook_id"):
        op(resource_id="cap-1", force=True, runbook_id="", token_provider=mock_token_provider)
    spy.assert_not_called()


def test_workspace_delete_runbook_optional(
    mock_token_provider: MagicMock, capture_audit_logs: pytest.LogCaptureFixture
) -> None:
    op, spy = _make_decorated("workspace", "delete")
    result = op(resource_id="ws-1", force=True, token_provider=mock_token_provider)
    assert result == "ran"
    spy.assert_called_once()
    records = [r for r in capture_audit_logs.records if r.message == "destructive_op"]
    assert len(records) == 1
    assert records[0].runbook_id is None
    assert records[0].resource_kind == "workspace"
    assert records[0].action == "delete"


def test_audit_record_fields(
    mock_token_provider: MagicMock, capture_audit_logs: pytest.LogCaptureFixture
) -> None:
    op, _ = _make_decorated("capacity", "pause")
    op(
        resource_id="cap-42",
        force=True,
        runbook_id="INC-9999",
        principal="sp-abc",
        token_provider=mock_token_provider,
    )
    records = [r for r in capture_audit_logs.records if r.message == "destructive_op"]
    assert len(records) == 1
    r = records[0]
    assert r.event == "destructive_op"
    assert r.resource_kind == "capacity"
    assert r.action == "pause"
    assert r.resource_id == "cap-42"
    assert r.principal == "sp-abc"
    assert r.force is True
    assert r.runbook_id == "INC-9999"
    assert isinstance(r.timestamp, str) and "T" in r.timestamp
    assert r.timestamp.endswith("+00:00") or r.timestamp.endswith("Z")


def test_principal_inferred_from_token_provider(
    mock_token_provider: MagicMock, capture_audit_logs: pytest.LogCaptureFixture
) -> None:
    mock_token_provider.last_credential_class.return_value = "ManagedIdentityCredential"
    op, _ = _make_decorated("workspace", "delete")
    op(resource_id="ws-1", force=True, token_provider=mock_token_provider)
    records = [r for r in capture_audit_logs.records if r.message == "destructive_op"]
    assert records[0].principal == "ManagedIdentityCredential"


def test_principal_unknown_when_no_token_provider(
    capture_audit_logs: pytest.LogCaptureFixture,
) -> None:
    op, _ = _make_decorated("workspace", "delete")
    op(resource_id="ws-1", force=True)
    records = [r for r in capture_audit_logs.records if r.message == "destructive_op"]
    assert records[0].principal == "unknown"


def test_correlation_id_propagates(
    mock_token_provider: MagicMock, capture_audit_logs: pytest.LogCaptureFixture
) -> None:
    token = set_correlation_id("corr-abc-123")
    try:
        op, _ = _make_decorated("workspace", "delete")
        op(resource_id="ws-1", force=True, token_provider=mock_token_provider)
    finally:
        reset_correlation_id(token)
    records = [r for r in capture_audit_logs.records if r.message == "destructive_op"]
    assert records[0].correlation_id == "corr-abc-123"


def test_audit_emitted_on_exception(
    mock_token_provider: MagicMock, capture_audit_logs: pytest.LogCaptureFixture
) -> None:
    """Audit-2026-05-07 W1.8: failed destructive op MUST emit an audit record.

    Falsifiability contract: this test FAILS against the pre-fix
    implementation that emitted ``logger.info`` only on success and left
    no trace when the wrapped op raised mid-execution. Real-world risk:
    a ``delete_folder`` that issued the DELETE then received a 5xx
    mutated the workspace AND left no audit trail. Now the decorator
    emits an ``outcome=failed`` record with ``exc_type`` so operators
    can correlate the audit ledger with stderr.
    """

    @destructive_op("workspace", "delete")
    def boom(*, force: bool, resource_id: str, token_provider=None) -> None:
        raise RuntimeError("HTTP 404")

    with pytest.raises(RuntimeError, match="HTTP 404"):
        boom(resource_id="ws-1", force=True, token_provider=mock_token_provider)
    records = [r for r in capture_audit_logs.records if r.message == "destructive_op"]
    assert len(records) == 1, (
        f"expected exactly one destructive_op record on failure path; got {len(records)}"
    )
    r = records[0]
    assert r.outcome == "failed"
    assert r.exc_type == "RuntimeError"
    assert r.resource_kind == "workspace"
    assert r.action == "delete"
    assert r.resource_id == "ws-1"
    assert r.force is True
    # T-3-04: failure record must not leak token / Authorization values.
    full_text = r.getMessage() + " " + repr(r.__dict__)
    assert "Bearer " not in full_text
    assert "test-token-xyz" not in full_text
    # Bounded message: the wrapped exception's str() must not be embedded
    # in the audit record (the operator-facing surface is the exc_type +
    # correlation id; the full traceback lives in stderr, not the
    # ledger).
    assert "HTTP 404" not in full_text


def test_audit_outcome_succeeded_on_clean_path(
    mock_token_provider: MagicMock, capture_audit_logs: pytest.LogCaptureFixture
) -> None:
    """Audit record on the clean path now carries ``outcome=succeeded``."""
    op, _ = _make_decorated("workspace", "delete")
    op(resource_id="ws-2", force=True, token_provider=mock_token_provider)
    records = [r for r in capture_audit_logs.records if r.message == "destructive_op"]
    assert len(records) == 1
    assert records[0].outcome == "succeeded"
    # exc_type field is intentionally absent on the success record.
    assert not hasattr(records[0], "exc_type") or records[0].__dict__.get("exc_type") is None


def test_no_sensitive_in_audit(
    mock_token_provider: MagicMock, capture_audit_logs: pytest.LogCaptureFixture
) -> None:
    # T-3-04: regardless of what the caller passes, the audit record never
    # contains a token or Authorization header value.
    op, _ = _make_decorated("workspace", "delete")
    op(
        resource_id="ws-1",
        force=True,
        principal="sp-abc",
        token_provider=mock_token_provider,
    )
    for r in capture_audit_logs.records:
        text = r.getMessage() + " " + repr(r.__dict__)
        assert "Bearer " not in text
        assert "test-token-xyz" not in text  # the fixture's fake token
