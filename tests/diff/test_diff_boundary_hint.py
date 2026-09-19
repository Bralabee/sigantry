"""DOCS-H-06 / D-19-05 -- snapshot-not-remediation trailer on `sigantry diff`.

The trailer fires when ``report.has_drift()`` is True AND ``--output human``
AND ``--no-hint`` is unset. Suppressed under ``--output json`` (preserves the
SemVer-pinned wire contract used by scheduled drift pipelines).

Pattern source: ``tests/sync/test_cli_apply_boundary_hint.py`` (Phase 17
canonical D-19-08 3-test contract).

LOAD-BEARING (Pitfall 1): the third test pins the JSON wire-contract
invariant -- ``templates/schedules/drift-check.yml:75-80`` +
``.github/workflows/drift-check.yml:60-68`` pipe ``sigantry diff
--output json --fail-on-drift > drift.json`` and feed ``drift.json`` to
a notification step. A trailer on stdout under ``--output json`` would
corrupt the JSON parser downstream and produce stale notifications. If a
future contributor removes that test or the gate, scheduled-drift
pipelines silently break.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

import sigantry_core.diff_cli as diff_cli
from sigantry_core.cli import app
from sigantry_core.sync.diff import DriftReport

runner = CliRunner()


def _write_dummy_manifest(tmp_path: Path) -> Path:
    """Write a minimal sync.yml so the manifest-load path does not error."""
    p = tmp_path / "sync.yml"
    p.write_text("schema_version: '1.0.0'\nitems: []\n", encoding="utf-8")
    return p


def _drift_report() -> DriftReport:
    """has_drift() == True (added populated)."""
    return DriftReport(
        schema_version="1.0.0",
        added=[
            {
                "logical_id": "x",
                "display_name": "X",
                "type": "Notebook",
                "folder_path": "/A",
            }
        ],
        removed=[],
        modified=[],
        unchanged=[],
    )


def _clean_report() -> DriftReport:
    """has_drift() == False (only unchanged populated)."""
    return DriftReport(
        schema_version="1.0.0",
        added=[],
        removed=[],
        modified=[],
        unchanged=[{"logical_id": "x"}],
    )


def test_diff_trailer_fires_on_drift_human_output(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """has_drift() True AND output=human -> trailer fires.

    --output defaults to 'human' (not specified explicitly). --fail-on-drift
    is unset, so the CLI exits 0 even with drift. The trailer text MUST
    contain the four anchor tokens that operators are expected to grep
    for: 'note:', 'snapshot', 'NOT auto-remediated', 'scheduled-drift.md'.
    """
    manifest = _write_dummy_manifest(tmp_path)
    monkeypatch.setattr(
        diff_cli, "diff_workspace_against_manifest", lambda *a, **k: _drift_report()
    )

    result = runner.invoke(
        app,
        [
            "diff",
            "-e",
            "prod",
            "--workspace-id",
            "ws-x",
            "--manifest",
            str(manifest),
        ],
    )

    # exit 0 because --fail-on-drift unset
    assert result.exit_code == 0, result.output
    assert "note:" in result.output
    assert "snapshot" in result.output
    assert "NOT auto-remediated" in result.output
    assert "scheduled-drift.md" in result.output


def test_diff_trailer_suppressed_when_no_drift(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """has_drift() False -> trailer suppressed.

    Clean report (only ``unchanged`` populated). The trailer MUST NOT
    fire -- there is no drift advice to surface.
    """
    manifest = _write_dummy_manifest(tmp_path)
    monkeypatch.setattr(
        diff_cli, "diff_workspace_against_manifest", lambda *a, **k: _clean_report()
    )

    result = runner.invoke(
        app,
        [
            "diff",
            "-e",
            "prod",
            "--workspace-id",
            "ws-x",
            "--manifest",
            str(manifest),
        ],
    )
    assert result.exit_code == 0, result.output
    assert "note:" not in result.output


def test_diff_trailer_suppressed_under_json_output(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """LOAD-BEARING: --output json -> trailer suppressed (preserves wire contract).

    The scheduled drift pipelines (``templates/schedules/drift-check.yml`` +
    ``.github/workflows/drift-check.yml``) capture ``sigantry diff --output
    json`` stdout into ``drift.json``. A trailer on stdout would corrupt
    the JSON parser downstream and produce stale notifications.

    The gate is ``output == "human"`` (positive whitelist), NOT ``output
    != "json"`` -- defensive against future ``--output yaml`` /
    ``--output csv`` modes.

    Falsifies the gate: if a contributor relaxes it to print under JSON,
    this test fails AND ``json.loads(result.output)`` would also fail at
    runtime in the scheduled pipeline.
    """
    manifest = _write_dummy_manifest(tmp_path)
    monkeypatch.setattr(
        diff_cli, "diff_workspace_against_manifest", lambda *a, **k: _drift_report()
    )

    result = runner.invoke(
        app,
        [
            "diff",
            "-e",
            "prod",
            "--workspace-id",
            "ws-x",
            "--manifest",
            str(manifest),
            "--output",
            "json",
        ],
    )
    assert result.exit_code == 0, result.output
    assert "note:" not in result.output
    # Sanity: the JSON document IS present on stdout (no accidental
    # over-suppression of the json branch).
    assert '"added"' in result.output
    # Stronger invariant: the stdout is a parseable JSON document.
    json.loads(result.output)
