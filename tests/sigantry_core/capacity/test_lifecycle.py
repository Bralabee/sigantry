"""Unit tests for sigantry_core.capacity.lifecycle (Plan 03-02 Task 2, WKSP-05).

Tests the destructive_op gate (Pitfall 11) for both suspend and resume:
- force=True is mandatory (DestructiveOpError otherwise, no ARM call fires)
- runbook_id is mandatory and non-empty (DestructiveOpError otherwise)
- Happy path: arm_client.send_arm_lro invoked with the correct ARM path
- Single audit record emitted on success with resource_kind=capacity
- Correlation id threads through from the active context
"""

from __future__ import annotations

import logging
from unittest.mock import MagicMock

import pytest

from sigantry_core.capacity import resume_capacity, suspend_capacity
from sigantry_core.client.logging import reset_correlation_id, set_correlation_id
from sigantry_core.governance import DestructiveOpError


class TestSuspend:
    def test_requires_force(
        self,
        mock_arm_client: MagicMock,
        mock_token_provider: MagicMock,
    ) -> None:
        with pytest.raises(DestructiveOpError, match="force=True"):
            suspend_capacity(
                mock_arm_client,
                "sub-1",
                "rg-1",
                "cap-1",
                force=False,
                runbook_id="INC-1",
                token_provider=mock_token_provider,
            )
        mock_arm_client.send_arm_lro.assert_not_called()

    def test_requires_runbook(
        self,
        mock_arm_client: MagicMock,
        mock_token_provider: MagicMock,
    ) -> None:
        with pytest.raises(DestructiveOpError, match="runbook_id"):
            suspend_capacity(
                mock_arm_client,
                "sub-1",
                "rg-1",
                "cap-1",
                force=True,
                runbook_id=None,
                token_provider=mock_token_provider,
            )
        mock_arm_client.send_arm_lro.assert_not_called()

    def test_empty_runbook_rejected(
        self,
        mock_arm_client: MagicMock,
        mock_token_provider: MagicMock,
    ) -> None:
        with pytest.raises(DestructiveOpError, match="runbook_id"):
            suspend_capacity(
                mock_arm_client,
                "sub-1",
                "rg-1",
                "cap-1",
                force=True,
                runbook_id="",
                token_provider=mock_token_provider,
            )

    def test_happy_path_calls_arm_lro(
        self,
        mock_arm_client: MagicMock,
        mock_token_provider: MagicMock,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        caplog.set_level(logging.INFO, logger="sigantry_core.governance.audit")
        suspend_capacity(
            mock_arm_client,
            "sub-1",
            "rg-1",
            "cap-1",
            force=True,
            runbook_id="INC-1234",
            token_provider=mock_token_provider,
        )
        mock_arm_client.send_arm_lro.assert_called_once_with(
            "POST",
            "/subscriptions/sub-1/resourceGroups/rg-1"
            "/providers/Microsoft.Fabric/capacities/cap-1/suspend",
        )
        records = [r for r in caplog.records if r.message == "destructive_op"]
        assert len(records) == 1
        assert records[0].resource_kind == "capacity"
        assert records[0].action == "pause"
        assert records[0].runbook_id == "INC-1234"
        assert records[0].force is True
        # Principal inferred from TokenProvider.last_credential_class
        assert records[0].principal == "MockCredential"

    def test_default_resource_id_format(
        self,
        mock_arm_client: MagicMock,
        mock_token_provider: MagicMock,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        caplog.set_level(logging.INFO, logger="sigantry_core.governance.audit")
        suspend_capacity(
            mock_arm_client,
            "sub-1",
            "rg-1",
            "cap-1",
            force=True,
            runbook_id="INC-1",
            token_provider=mock_token_provider,
        )
        records = [r for r in caplog.records if r.message == "destructive_op"]
        assert (
            records[0].resource_id == "/subscriptions/sub-1/resourceGroups/rg-1"
            "/providers/Microsoft.Fabric/capacities/cap-1"
        )

    def test_no_sensitive_in_audit(
        self,
        mock_arm_client: MagicMock,
        mock_token_provider: MagicMock,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """T-3-04: audit record must not carry Authorization header / token."""
        caplog.set_level(logging.INFO, logger="sigantry_core.governance.audit")
        suspend_capacity(
            mock_arm_client,
            "sub-1",
            "rg-1",
            "cap-1",
            force=True,
            runbook_id="INC-1234",
            token_provider=mock_token_provider,
        )
        records = [r for r in caplog.records if r.message == "destructive_op"]
        # Flatten every stored value in the record (excluding pytest internals)
        # and assert no token-ish string appears.
        snapshot = repr(records[0].__dict__)
        assert "test-token-xyz" not in snapshot
        assert "Bearer " not in snapshot


class TestResume:
    def test_happy_path_calls_arm_lro(
        self,
        mock_arm_client: MagicMock,
        mock_token_provider: MagicMock,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        caplog.set_level(logging.INFO, logger="sigantry_core.governance.audit")
        resume_capacity(
            mock_arm_client,
            "sub-1",
            "rg-1",
            "cap-1",
            force=True,
            runbook_id="INC-9999",
            token_provider=mock_token_provider,
        )
        mock_arm_client.send_arm_lro.assert_called_once_with(
            "POST",
            "/subscriptions/sub-1/resourceGroups/rg-1"
            "/providers/Microsoft.Fabric/capacities/cap-1/resume",
        )
        records = [r for r in caplog.records if r.message == "destructive_op"]
        assert records[0].action == "resume"
        assert records[0].runbook_id == "INC-9999"

    def test_requires_force(
        self,
        mock_arm_client: MagicMock,
        mock_token_provider: MagicMock,
    ) -> None:
        with pytest.raises(DestructiveOpError, match="force=True"):
            resume_capacity(
                mock_arm_client,
                "sub-1",
                "rg-1",
                "cap-1",
                force=False,
                runbook_id="INC-1",
                token_provider=mock_token_provider,
            )
        mock_arm_client.send_arm_lro.assert_not_called()

    def test_requires_runbook(
        self,
        mock_arm_client: MagicMock,
        mock_token_provider: MagicMock,
    ) -> None:
        with pytest.raises(DestructiveOpError, match="runbook_id"):
            resume_capacity(
                mock_arm_client,
                "sub-1",
                "rg-1",
                "cap-1",
                force=True,
                runbook_id=None,
                token_provider=mock_token_provider,
            )
        mock_arm_client.send_arm_lro.assert_not_called()


class TestCorrelation:
    def test_correlation_id_threads_into_audit(
        self,
        mock_arm_client: MagicMock,
        mock_token_provider: MagicMock,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        caplog.set_level(logging.INFO, logger="sigantry_core.governance.audit")
        token = set_correlation_id("corr-capacity-1")
        try:
            suspend_capacity(
                mock_arm_client,
                "s",
                "rg",
                "c",
                force=True,
                runbook_id="INC-1",
                token_provider=mock_token_provider,
            )
        finally:
            reset_correlation_id(token)
        records = [r for r in caplog.records if r.message == "destructive_op"]
        assert records[0].correlation_id == "corr-capacity-1"
