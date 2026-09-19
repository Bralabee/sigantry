"""CliRunner-driven tests for ``sigantry diff`` (Plan 13-06 / Task 2 / D-26).

Exercises the exit-code semantics:

* 0 -- clean OR drift without ``--fail-on-drift``.
* 1 -- drift detected AND ``--fail-on-drift`` set.
* 2 -- operational error (manifest validation, workspace not found,
  unexpected exception).

Each test monkeypatches
``sigantry_core.diff_cli.diff_workspace_against_manifest`` so the test
exercises the CLI surface without spinning up a respx-backed
:class:`FabricRestClient`. The diff algorithm itself is exercised by
``tests/sync/test_diff.py`` (Task 1).
"""

from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

import sigantry_core.diff_cli as diff_cli
from sigantry_core.cli import app
from sigantry_core.sync.diff import DriftReport
from sigantry_core.sync.errors import ManifestValidationError

runner = CliRunner()


def _write_dummy_manifest(tmp_path: Path) -> Path:
    """Write a minimal sync.yml so the --manifest path resolves to a real file.

    Tests that monkeypatch ``diff_workspace_against_manifest`` never
    actually parse the manifest; we just need a valid file path so
    Typer's path resolution succeeds.
    """
    p = tmp_path / "sync.yml"
    p.write_text(
        "schema_version: '1.0.0'\nitems: []\n",
        encoding="utf-8",
    )
    return p


def _clean_report() -> DriftReport:
    return DriftReport(
        schema_version="1.0.0",
        added=[],
        removed=[],
        modified=[],
        unchanged=[{"logical_id": "x"}],
    )


def _drift_report() -> DriftReport:
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


def test_diff_help_lists_flags() -> None:
    """``sigantry diff --help`` lists the five locked flags (env is a flag, not positional).

    The plan pseudo-code described ``sigantry diff <env>`` (positional) but
    the implemented surface uses ``-e/--environment <env>``. Typer's
    ``add_typer`` + single-callback + positional ``Argument`` pattern
    clashes with Click's COMMAND slot when operators type the positional
    BEFORE the options (the natural ergonomic order); see the docstring
    on ``sigantry_core.diff_cli`` for the full rationale. UAT-FOUND
    follow-up #4 from 2026-04-29 HANDOFF: aligned this docstring with
    reality (was stale: "four locked flags + positional env").
    """
    result = runner.invoke(app, ["diff", "--help"])
    assert result.exit_code == 0
    text = result.output
    assert "--workspace-id" in text
    assert "--manifest" in text
    assert "--output" in text
    assert "--fail-on-drift" in text
    assert "--environment" in text


def test_diff_clean_exit_0(tmp_path: Path, monkeypatch) -> None:
    """No drift -> exit 0 even WITH --fail-on-drift set."""
    manifest = _write_dummy_manifest(tmp_path)
    monkeypatch.setattr(
        diff_cli, "diff_workspace_against_manifest", lambda *a, **k: _clean_report()
    )
    result = runner.invoke(
        app,
        [
            "diff",
            "--environment",
            "prod",
            "--workspace-id",
            "ws-x",
            "--manifest",
            str(manifest),
            "--fail-on-drift",
        ],
    )
    assert result.exit_code == 0, result.output


def test_diff_drift_with_fail_on_drift_exit_1(tmp_path: Path, monkeypatch) -> None:
    """drift + --fail-on-drift -> exit 1 (D-26 CI-gating contract)."""
    manifest = _write_dummy_manifest(tmp_path)
    monkeypatch.setattr(
        diff_cli, "diff_workspace_against_manifest", lambda *a, **k: _drift_report()
    )
    result = runner.invoke(
        app,
        [
            "diff",
            "--environment",
            "prod",
            "--workspace-id",
            "ws-x",
            "--manifest",
            str(manifest),
            "--fail-on-drift",
        ],
    )
    assert result.exit_code == 1, result.output


def test_diff_drift_without_fail_on_drift_exit_0(tmp_path: Path, monkeypatch) -> None:
    """drift WITHOUT --fail-on-drift -> exit 0 (drift surfaced via output, not gate)."""
    manifest = _write_dummy_manifest(tmp_path)
    monkeypatch.setattr(
        diff_cli, "diff_workspace_against_manifest", lambda *a, **k: _drift_report()
    )
    result = runner.invoke(
        app,
        [
            "diff",
            "--environment",
            "prod",
            "--workspace-id",
            "ws-x",
            "--manifest",
            str(manifest),
        ],
    )
    assert result.exit_code == 0, result.output


def test_diff_workspace_not_found_exits_2(tmp_path: Path, monkeypatch) -> None:
    """Operational error -> exit 2 (D-26).

    A bare RuntimeError simulates a workspace-not-found / auth-failure
    bubbled up from :func:`diff_workspace_against_manifest`. The CLI
    treats any unexpected exception as exit 2 so CI runners can branch
    distinctly from the validation-error / drift cases.
    """
    manifest = _write_dummy_manifest(tmp_path)

    def _raise(*a, **k):
        raise RuntimeError("workspace ws-x not found")

    monkeypatch.setattr(diff_cli, "diff_workspace_against_manifest", _raise)
    result = runner.invoke(
        app,
        [
            "diff",
            "--environment",
            "prod",
            "--workspace-id",
            "ws-x",
            "--manifest",
            str(manifest),
        ],
    )
    assert result.exit_code == 2, result.output


def test_diff_manifest_validation_exits_2(tmp_path: Path, monkeypatch) -> None:
    """ManifestValidationError -> exit 2 (operational error per D-26).

    Validation errors are operational (the manifest is malformed; not a
    Fabric drift) and so map to exit 2 -- distinct from the drift+gate
    exit 1 contract.
    """
    manifest = _write_dummy_manifest(tmp_path)

    def _raise(*a, **k):
        raise ManifestValidationError(
            "bad manifest",
            violations=[
                {
                    "field": "items.0.target_folder",
                    "reason": "depth > 10",
                    "decision_id": "D-05/Council-D-3",
                    "severity": "error",
                }
            ],
        )

    monkeypatch.setattr(diff_cli, "diff_workspace_against_manifest", _raise)
    result = runner.invoke(
        app,
        [
            "diff",
            "--environment",
            "prod",
            "--workspace-id",
            "ws-x",
            "--manifest",
            str(manifest),
        ],
    )
    assert result.exit_code == 2, result.output


def test_diff_json_output(tmp_path: Path, monkeypatch) -> None:
    """``--output json`` emits parseable JSON containing schema_version + buckets."""
    manifest = _write_dummy_manifest(tmp_path)
    monkeypatch.setattr(
        diff_cli, "diff_workspace_against_manifest", lambda *a, **k: _drift_report()
    )
    result = runner.invoke(
        app,
        [
            "diff",
            "--environment",
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
    # The Rich console wraps print_json output but the JSON body is
    # always parseable; locate the first '{' in the output and parse from
    # there to skip any banner / warning lines.
    payload_start = result.output.index("{")
    payload = json.loads(result.output[payload_start:])
    assert payload["schema_version"] == "1.0.0"
    assert "added" in payload and len(payload["added"]) == 1
    assert "removed" in payload
    assert "modified" in payload
    assert "unchanged" in payload


def test_diff_invalid_output_format_exits_2(tmp_path: Path, monkeypatch) -> None:
    """``--output garbage`` -> exit 2 with explicit error message."""
    manifest = _write_dummy_manifest(tmp_path)
    # Even though we monkey-patch the diff function, the CLI must reject
    # the invalid output format BEFORE calling it. We assert the patch
    # was never invoked by raising loudly inside.
    sentinel = {"called": False}

    def _shouldnt_be_called(*a, **k):
        sentinel["called"] = True
        return _clean_report()

    monkeypatch.setattr(diff_cli, "diff_workspace_against_manifest", _shouldnt_be_called)
    result = runner.invoke(
        app,
        [
            "diff",
            "--environment",
            "prod",
            "--workspace-id",
            "ws-x",
            "--manifest",
            str(manifest),
            "--output",
            "garbage",
        ],
    )
    assert result.exit_code == 2, result.output
    assert sentinel["called"] is False


def test_root_cli_registers_diff_as_15th_subapp() -> None:
    """``diff`` is registered as the 15th subapp, AFTER ``sync``.

    The exact registration order through ``diff`` is part of the public
    CLI contract: the 13 prior subapps + ``sync`` (Plan 13-04) + ``diff``
    (Plan 13-06) in that order. Subsequent plans append additional
    subapps (Plan 14-01 appends ``config`` 16th; Plan 14-06 will append
    ``pr-bot`` 17th). Tests for those subapps own their own ordering
    assertions; this test pins the prefix through ``diff``.

    Inserting ``diff`` anywhere else (or removing one of the 14 prior
    subapps) breaks an operator's mental model of the CLI surface.
    """
    names = [g.name for g in app.registered_groups]
    expected_through_diff = [
        "workspace",
        "capacity",
        "label-sync",
        "rbac-audit",
        "tenant-settings",
        "deploy",
        "fabric-item",
        "git",
        "variable-library",
        "env",
        "dq",
        "doctor",
        "release",
        "sync",
        "diff",
    ]
    # Prefix through diff must match exactly; trailing subapps (added by
    # Plans 14-01+) are tolerated and asserted by their own plan's tests.
    assert names[: len(expected_through_diff)] == expected_through_diff, (
        f"prefix through diff diverged; got {names}"
    )
    assert names.index("diff") == 14, f"diff must be the 15th subapp (zero-indexed 14); got {names}"
