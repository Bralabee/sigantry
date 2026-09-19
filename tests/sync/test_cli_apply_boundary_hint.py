"""ADR-0012 / D-26-bis -- the operator-hint trailer on `sigantry sync apply`.

The trailer fires when ``items_packaged > 0 AND folders_created > 0 AND
items_moved == 0`` -- the signature of "first-time project setup, items
staged but not published". On idempotent re-runs (``folders_created == 0``)
the trailer is suppressed. These three tests pin those edges so a regression
where the trailer either never fires or fires on every successful run gets
caught at CI time.

Surfaced by the 2026-05-01 live brownfield test against
COE_F_SBDEVOPS_POC: an operator running ``sync apply`` on a new project
folder reasonably read ``items_packaged=1`` as "1 item deployed" when in
fact ``sync apply`` is folder-reconcile only. ADR-0012 formalises the
boundary; this trailer surfaces it at the moment of confusion.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from sigantry_core.sync import cli as sync_cli_mod
from sigantry_core.sync.apply import SyncApplyReport
from sigantry_core.sync.cli import sync_app

runner = CliRunner()


def _stub_apply_returning(report: SyncApplyReport, monkeypatch: pytest.MonkeyPatch) -> None:
    """Stub ``apply_sync`` to return the given report verbatim.

    The CLI's success / trailer print branch runs after the engine returns,
    so we can construct any ``SyncApplyReport`` shape and assert against
    the resulting stdout without needing a working tenant or staging tree.
    """

    def fake_apply(*_a, **_k):
        return report

    monkeypatch.setattr(sync_cli_mod, "apply_sync", fake_apply)


def _bypass_preview_warning(monkeypatch: pytest.MonkeyPatch) -> None:
    """Acknowledge the preview-API gate so it doesn't pollute stdout."""
    monkeypatch.setenv("FDT_WORKFLOW__PREVIEW_APIS_ACKNOWLEDGED", "true")
    sync_cli_mod._PREVIEW_WARNING_EMITTED.clear()


@pytest.fixture
def synthetic_manifest(tmp_path: Path) -> Path:
    p = tmp_path / "synthetic-sync.yml"
    p.write_text("schema_version: '1.0.0'\nitems: []\n", encoding="utf-8")
    return p


def test_trailer_fires_on_first_time_project_setup(
    synthetic_manifest: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """items_packaged > 0 AND folders_created > 0 AND items_moved == 0.

    This is the exact signature of the 2026-05-01 live test that surfaced
    the design boundary: a fresh project folder (`si_dataops_testing_project/01_Notebooks`)
    was created, the scaffold notebook was staged locally, and zero existing
    items were moved -- because the new notebook didn't exist in the
    workspace yet for `reconcile_folders_from_repo` to find. The trailer
    must point the operator at `deploy run` before they spend an hour
    wondering why their notebook didn't land.
    """
    _bypass_preview_warning(monkeypatch)
    _stub_apply_returning(
        SyncApplyReport(
            workspace_id="ws-synthetic",
            manifest_path=str(synthetic_manifest),
            items_packaged=1,
            folders_created=2,
            items_moved=0,
            deploy_record_release_id="sync-2026-05-01T00-00-00Z",
            outcome="succeeded",
            failure_reason=None,
            staging_dir=None,
        ),
        monkeypatch,
    )

    result = runner.invoke(
        sync_app,
        [
            "apply",
            "--manifest",
            str(synthetic_manifest),
            "--workspace-id",
            "ws-synthetic",
        ],
    )

    # Rich console wraps long lines at terminal width, so multi-word
    # substrings can be split mid-phrase in the captured output. Anchor
    # on stable single-word tokens that survive wrapping.
    assert result.exit_code == 0, result.output
    assert "sync apply succeeded" in result.output
    assert "note:" in result.output
    assert "1 manifest item" in result.output
    assert "sigantry deploy run" in result.output
    assert "apply.md" in result.output


def test_trailer_suppressed_on_idempotent_rerun(
    synthetic_manifest: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """folders_created == 0 -- second run after the first has already set up
    the folders. The hint MUST NOT fire here; it's noise.
    """
    _bypass_preview_warning(monkeypatch)
    _stub_apply_returning(
        SyncApplyReport(
            workspace_id="ws-synthetic",
            manifest_path=str(synthetic_manifest),
            items_packaged=1,
            folders_created=0,
            items_moved=0,
            deploy_record_release_id="sync-2026-05-01T00-00-01Z",
            outcome="succeeded",
            failure_reason=None,
            staging_dir=None,
        ),
        monkeypatch,
    )

    result = runner.invoke(
        sync_app,
        [
            "apply",
            "--manifest",
            str(synthetic_manifest),
            "--workspace-id",
            "ws-synthetic",
        ],
    )

    assert result.exit_code == 0, result.output
    assert "sync apply succeeded" in result.output
    assert "note:" not in result.output
    assert "deploy run" not in result.output


def test_trailer_suppressed_when_existing_items_were_moved(
    synthetic_manifest: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """items_moved > 0 -- existing items got reparented per the manifest.
    No first-time-deploy concern; the items are already in the workspace.
    """
    _bypass_preview_warning(monkeypatch)
    _stub_apply_returning(
        SyncApplyReport(
            workspace_id="ws-synthetic",
            manifest_path=str(synthetic_manifest),
            items_packaged=3,
            folders_created=1,
            items_moved=2,
            deploy_record_release_id="sync-2026-05-01T00-00-02Z",
            outcome="succeeded",
            failure_reason=None,
            staging_dir=None,
        ),
        monkeypatch,
    )

    result = runner.invoke(
        sync_app,
        [
            "apply",
            "--manifest",
            str(synthetic_manifest),
            "--workspace-id",
            "ws-synthetic",
        ],
    )

    assert result.exit_code == 0, result.output
    assert "sync apply succeeded" in result.output
    assert "note:" not in result.output
    assert "deploy run" not in result.output


def test_trailer_suppressed_when_with_publish_set(
    synthetic_manifest: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``--with-publish`` suppresses the D-26-bis trailer (D-17-08).

    Field signature is the canonical first-time-setup case
    (``items_packaged > 0 AND folders_created > 0 AND items_moved == 0``)
    that fires the trailer in :func:`test_trailer_fires_on_first_time_project_setup`.
    Under ``--with-publish`` the publish DID happen, so the trailer's
    "use ``sigantry deploy run``" hint would mislead the operator into
    double-publishing.

    Uses an ``_ALL_``-only ``parameters.yml`` so the D-17-02 multi-env
    gate does NOT intervene before reaching the trailer code path.
    """
    _bypass_preview_warning(monkeypatch)
    _stub_apply_returning(
        SyncApplyReport(
            workspace_id="ws-synthetic",
            manifest_path=str(synthetic_manifest),
            items_packaged=1,
            folders_created=2,
            items_moved=0,
            deploy_record_release_id="sync-publish-2026-05-01T00-00-03Z",
            outcome="succeeded",
            failure_reason=None,
            staging_dir=None,
        ),
        monkeypatch,
    )

    params_yml = tmp_path / "p.yml"
    params_yml.write_text(
        "find_replace:\n"
        "  - find_value: 'placeholder'\n"
        "    replace_value:\n"
        "      _ALL_: '$workspace.$id'\n",
        encoding="utf-8",
    )

    result = runner.invoke(
        sync_app,
        [
            "apply",
            "--manifest",
            str(synthetic_manifest),
            "--workspace-id",
            "ws-synthetic",
            "--with-publish",
            "--params",
            str(params_yml),
        ],
    )

    assert result.exit_code == 0, result.output
    assert "sync apply succeeded" in result.output
    # Trailer-specific tokens MUST be absent under --with-publish.
    assert "staged locally" not in result.output
    assert "sigantry deploy run" not in result.output
