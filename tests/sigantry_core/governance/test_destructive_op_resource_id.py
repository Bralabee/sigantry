"""Audit-2026-05-08 review follow-up (BL-01) — falsifiability tests for
``@destructive_op``'s ``resource_id`` propagation across every public
``delete_*`` verb in ``sigantry_core``.

Pre-fix: five public verbs accepted ``resource_id`` purely as decorator
plumbing then *discarded* the value via ``_ = resource_id or X`` --
when an operator ran e.g. ``delete_workspace(client, workspace_id="abc",
force=True)``, the on-disk ``destructive_ops.jsonl`` line carried
``"resource_id": null``. The audit answered "WHAT happened" but not
"WHICH resource". The W1.7 meta-gate (``test_destructive_verbs_decorated.py``)
checked decorator *presence*, not audit *content*, so the bug was
invisible to the gate.

Post-fix: the decorator carries an opt-in ``resource_arg`` parameter
that names the wrapped function's positional / keyword arg(s)
identifying the target resource. The decorator binds the call args
via ``inspect.signature`` and threads the value into
``DestructiveOpRecord.resource_id``. This file is the *behavioural*
counterpart to the structural meta-gate: it actually invokes each
decorated verb with mock clients and reads the JSONL line back.

Falsifiability:

- Reverting any of the 5 ``resource_arg=...`` decorations to the
  bare ``@destructive_op("kind", "action")`` form flips the relevant
  parametrised test from PASS to FAIL with ``"resource_id": null`` in
  the captured record.
- Removing the ``inspect.signature`` lookup in
  ``governance/destructive.py`` (so the decorator stops resolving
  ``resource_arg`` to the bound value) flips all 5 tests
  identically.
"""

from __future__ import annotations

import importlib
import json
import logging
from pathlib import Path
from typing import Any, ClassVar

import pytest

from sigantry_core.deploy.variable_library import delete_variable_library
from sigantry_core.governance.rbac import delete_role_assignment
from sigantry_core.workspace.core import delete_workspace
from sigantry_core.workspace.folders import delete_folder
from sigantry_core.workspace.items import delete_item

# ``sigantry_core.governance`` re-exports an ``audit`` function from ``rbac``;
# ``import sigantry_core.governance.audit`` resolves to that function instead
# of the submodule. ``importlib.import_module`` always returns the module.
audit_module = importlib.import_module("sigantry_core.governance.audit")


class _StubResponse:
    """Minimal RestResponse-shaped object for SDK calls that read .json_body."""

    json_body: ClassVar[dict[str, Any]] = {}


class _StubClient:
    """Captures ``.send`` calls instead of issuing HTTP."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    def send(self, method: str, url: str, **_: Any) -> _StubResponse:
        self.calls.append((method, url))
        return _StubResponse()


@pytest.fixture
def isolated_audit_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Redirect the destructive-op JSONL to a tmp path.

    ``emit_destructive_op_record`` reads
    ``sigantry_core.governance.audit._DEFAULT_AUDIT_DIR`` at call time
    via the module-level binding -- patching the audit module's
    reference is enough to redirect every emit during the test.
    """
    audit_root = tmp_path / "audit"
    monkeypatch.setattr(audit_module, "_DEFAULT_AUDIT_DIR", audit_root)
    return audit_root


def _read_destructive_ops(audit_dir: Path) -> list[dict[str, Any]]:
    """Return the parsed destructive_ops.jsonl records (most-recent last)."""
    jsonl = audit_dir / "destructive_ops.jsonl"
    assert jsonl.exists(), f"expected ledger at {jsonl}"
    return [
        json.loads(line) for line in jsonl.read_text(encoding="utf-8").splitlines() if line.strip()
    ]


# --- delete_workspace --------------------------------------------------------


def test_delete_workspace_records_workspace_id_as_resource_id(
    isolated_audit_dir: Path,
) -> None:
    """``delete_workspace(client, workspace_id="X", force=True)`` writes resource_id=X."""
    client = _StubClient()
    delete_workspace(client, workspace_id="ws-abc-123", force=True)

    records = _read_destructive_ops(isolated_audit_dir)
    assert len(records) == 1
    assert records[0]["resource_kind"] == "workspace"
    assert records[0]["action"] == "delete"
    assert records[0]["resource_id"] == "ws-abc-123", (
        "destructive-op audit must capture the workspace_id; pre-BL-01-fix "
        "this read 'null' because the decorator did not look at the "
        "positional ``workspace_id`` arg"
    )
    assert records[0]["outcome"] == "succeeded"


def test_delete_workspace_records_workspace_id_when_passed_positionally(
    isolated_audit_dir: Path,
) -> None:
    """Positional binding works too — ``inspect.signature.bind_partial`` covers both."""
    client = _StubClient()
    delete_workspace(client, "ws-positional-456", force=True)
    records = _read_destructive_ops(isolated_audit_dir)
    assert records[0]["resource_id"] == "ws-positional-456"


def test_delete_workspace_explicit_resource_id_kwarg_wins(
    isolated_audit_dir: Path,
) -> None:
    """An explicit ``resource_id=`` kwarg overrides the inferred value.

    Preserves backward compatibility for callers that already pass
    ``resource_id=...`` (e.g. when the audit-canonical id differs from
    the SDK-positional id, like the ARM resource id for capacities).
    """
    client = _StubClient()
    delete_workspace(
        client,
        workspace_id="ws-from-arg",
        force=True,
        resource_id="canonical://custom/id",
    )
    records = _read_destructive_ops(isolated_audit_dir)
    assert records[0]["resource_id"] == "canonical://custom/id"


# --- delete_folder ----------------------------------------------------------


def test_delete_folder_records_folder_id_as_resource_id(
    isolated_audit_dir: Path,
) -> None:
    client = _StubClient()
    delete_folder(client, "ws-1", "fld-deadbeef", force=True)

    records = _read_destructive_ops(isolated_audit_dir)
    assert records[0]["resource_kind"] == "folder"
    assert records[0]["resource_id"] == "fld-deadbeef"


# --- delete_item ------------------------------------------------------------


def test_delete_item_records_item_id_as_resource_id(
    isolated_audit_dir: Path,
) -> None:
    client = _StubClient()
    delete_item(client, "ws-1", "itm-cafebabe", force=True)

    records = _read_destructive_ops(isolated_audit_dir)
    assert records[0]["resource_kind"] == "item"
    assert records[0]["resource_id"] == "itm-cafebabe"


# --- delete_role_assignment (compound resource: workspace_id/principal_id) --


def test_delete_role_assignment_records_compound_resource_id(
    isolated_audit_dir: Path,
) -> None:
    """RBAC role-assignment is identified by the (workspace, principal) pair.

    Pre-fix, ``delete_role_assignment`` had no ``resource_id`` kwarg at
    all, so the audit recorded ``resource_id=null`` for every revoke
    with no operator-side workaround. Post-fix the decorator joins the
    two named args with ``/`` so post-incident triage can answer
    "whose role was revoked, on which workspace".
    """
    client = _StubClient()
    delete_role_assignment(client, "ws-prod-1", "user-abc-uuid", force=True)

    records = _read_destructive_ops(isolated_audit_dir)
    assert records[0]["resource_kind"] == "role_assignment"
    assert records[0]["resource_id"] == "ws-prod-1/user-abc-uuid"


# --- delete_variable_library ------------------------------------------------


def test_delete_variable_library_records_vl_id_as_resource_id(
    isolated_audit_dir: Path,
) -> None:
    client = _StubClient()
    delete_variable_library(
        client,
        workspace_id="ws-1",
        variable_library_id="vl-9999",
        force=True,
    )

    records = _read_destructive_ops(isolated_audit_dir)
    assert records[0]["resource_kind"] == "variable_library"
    assert records[0]["resource_id"] == "vl-9999"


# --- failure-path coverage --------------------------------------------------


def test_delete_workspace_records_resource_id_even_on_sdk_failure(
    isolated_audit_dir: Path,
) -> None:
    """The W1.8 invariant (audit on success AND failure) preserves resource_id.

    Pre-BL-01-fix the failure-path audit also dropped resource_id —
    same bug, both branches. Post-fix the decorator threads resource_id
    through both ``logger.warning`` (failure) and ``logger.info``
    (success) branches.
    """

    class _FailingClient:
        def send(self, method: str, url: str, **_: Any) -> Any:
            raise RuntimeError("simulated SDK 5xx")

    client = _FailingClient()
    with pytest.raises(RuntimeError, match="simulated SDK"):
        delete_workspace(client, workspace_id="ws-fail-789", force=True)

    records = _read_destructive_ops(isolated_audit_dir)
    assert len(records) == 1
    assert records[0]["resource_id"] == "ws-fail-789"
    assert records[0]["outcome"] == "failed"
    assert records[0]["exc_type"] == "RuntimeError"


# --- structured-logger event also carries resource_id ----------------------


def test_decorator_structured_log_event_carries_resource_id(
    isolated_audit_dir: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """The ``logger.info("destructive_op", extra=...)`` event mirrors the JSONL.

    Operators with structured-logging pipelines (journald, datadog,
    log analytics) consume the *event* payload, not the JSONL ledger.
    The pre-fix bug also dropped resource_id from the event extras --
    re-pin so a future regression in either channel surfaces here.
    """
    caplog.set_level(logging.INFO, logger="sigantry_core.governance.audit")
    client = _StubClient()
    delete_item(client, "ws-1", "itm-event-test", force=True)

    matched = [r for r in caplog.records if r.msg == "destructive_op"]
    assert matched, "expected at least one destructive_op log event"
    # The event with outcome=succeeded must carry resource_id.
    succeeded = [r for r in matched if getattr(r, "outcome", None) == "succeeded"]
    assert succeeded, "expected a succeeded destructive_op event"
    assert getattr(succeeded[0], "resource_id", None) == "itm-event-test"
