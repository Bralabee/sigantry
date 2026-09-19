"""DOCS-H-05 / D-19-04 -- operator-hint trailer on `sigantry sync pull`.

The trailer fires when ``report.items_pulled > 0`` (D-19-04). It
reminds the operator to commit ``sync.yml`` + sources before next
session so ``logical_id`` idempotency survives across checkouts.

Suppression hooks:

* ``items_pulled == 0`` -- no-op pull, no advice needed.
* ``--no-hint`` -- operator-explicit (CI-friendly).

Pattern source: ``tests/sync/test_cli_apply_boundary_hint.py``
(Phase 17 canonical 3-test contract D-19-08). Each test calls
``_bypass_preview_warning`` to suppress the preview-API warning that
``pull_cmd`` emits before the engine (Pitfall 6) and uses
``_make_pull_report`` to centralise ``WorkspaceSnapshot`` minimal-shape
construction (Pitfall 7).
"""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from sigantry_core.sync import cli as sync_cli_mod
from sigantry_core.sync.cli import sync_app
from sigantry_core.sync.pull import SyncPullReport
from sigantry_core.sync.snapshot import WorkspaceSnapshot

runner = CliRunner()


def _stub_pull_returning(report: SyncPullReport, monkeypatch: pytest.MonkeyPatch) -> None:
    """Stub ``pull_workspace`` to return the given report verbatim.

    The CLI's success / trailer print branch runs after the engine
    returns, so we can construct any ``SyncPullReport`` shape and
    assert against the resulting stdout without needing a working
    tenant or a real source workspace.
    """

    def fake_pull(*_a, **_k):
        return report

    monkeypatch.setattr(sync_cli_mod, "pull_workspace", fake_pull)


def _bypass_preview_warning(monkeypatch: pytest.MonkeyPatch) -> None:
    """Acknowledge the preview-API gate so the warning doesn't pollute stdout.

    Pitfall 6: ``pull_cmd`` calls ``_emit_preview_warning_once()`` at
    line 397 BEFORE the engine. Without this bypass the preview-API
    warning fires first and the ``"note:"`` substring assertion can
    pass on the warning text rather than on the trailer we are pinning.
    """
    monkeypatch.setenv("FDT_WORKFLOW__PREVIEW_APIS_ACKNOWLEDGED", "true")
    sync_cli_mod._PREVIEW_WARNING_EMITTED.clear()


def _make_pull_report(items_pulled: int, tmp_path: Path) -> SyncPullReport:
    """Build a stub :class:`SyncPullReport` with the given ``items_pulled``.

    Pitfall 7: centralise :class:`WorkspaceSnapshot` minimal-shape
    construction here so a single edit updates all 3 tests if the
    snapshot's required fields evolve. The trailer code path only
    inspects ``items_pulled``; the snapshot fields are constructed as
    minimal-shape empties to satisfy Pydantic ``extra="forbid"``.

    ``WorkspaceSnapshot`` is a Pydantic v2 ``BaseModel``; the
    canonical introspection idiom is
    ``WorkspaceSnapshot.model_fields.keys()`` (NOT
    ``dataclasses.fields()``).
    """
    return SyncPullReport(
        workspace_id="ws-x",
        into=tmp_path / "fabric-iac",
        items_pulled=items_pulled,
        sync_yml_path=tmp_path / "fabric-iac" / "sync.yml",
        snapshot=WorkspaceSnapshot(
            schema_version="1.0.0",
            workspace_id="ws-x",
            folder_path_index={},
            item_to_folder={},
            folders_by_id={},
            items_by_id={},
        ),
    )


def test_pull_trailer_fires_when_items_pulled_positive(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``items_pulled > 0`` -> the D-19-04 trailer fires.

    The trailer reminds the operator to commit ``sync.yml`` + sources
    so ``logical_id`` idempotency survives the next pull from a
    different checkout. Anchor on stable single-word tokens that
    survive Rich's terminal-width line wrapping.
    """
    _bypass_preview_warning(monkeypatch)
    _stub_pull_returning(_make_pull_report(27, tmp_path), monkeypatch)

    result = runner.invoke(
        sync_app,
        [
            "pull",
            "--workspace-id",
            "ws-x",
            "--into",
            str(tmp_path / "fabric-iac"),
        ],
    )
    assert result.exit_code == 0, result.output
    assert "sync pull succeeded" in result.output
    assert "note:" in result.output
    assert "27 item" in result.output
    assert "commit" in result.output
    assert "logical_id" in result.output
    assert "pull.md" in result.output


def test_pull_trailer_suppressed_when_no_items_pulled(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``items_pulled == 0`` -> the trailer is suppressed (no-op pull).

    A no-op pull does not need the commit reminder; it would be noise.
    The existing ``sync pull succeeded`` line STILL fires. This test
    is the regression guard: it would fire if a future change started
    printing the trailer unconditionally.
    """
    _bypass_preview_warning(monkeypatch)
    _stub_pull_returning(_make_pull_report(0, tmp_path), monkeypatch)

    result = runner.invoke(
        sync_app,
        [
            "pull",
            "--workspace-id",
            "ws-x",
            "--into",
            str(tmp_path / "fabric-iac"),
        ],
    )
    assert result.exit_code == 0, result.output
    assert "sync pull succeeded" in result.output
    assert "note:" not in result.output
    assert "commit" not in result.output


def test_pull_trailer_suppressed_under_no_hint_flag(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``--no-hint`` suppresses the trailer even when the field signature matches.

    Operator-explicit suppression for CI runners that do not want the
    trailer in their captured stdout. ``items_pulled > 0`` here, so
    without ``--no-hint`` the trailer would fire (per
    :func:`test_pull_trailer_fires_when_items_pulled_positive`).
    """
    _bypass_preview_warning(monkeypatch)
    _stub_pull_returning(_make_pull_report(5, tmp_path), monkeypatch)

    result = runner.invoke(
        sync_app,
        [
            "pull",
            "--workspace-id",
            "ws-x",
            "--into",
            str(tmp_path / "fabric-iac"),
            "--no-hint",
        ],
    )
    assert result.exit_code == 0, result.output
    assert "sync pull succeeded" in result.output
    assert "note:" not in result.output
