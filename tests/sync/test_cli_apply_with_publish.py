"""CliRunner tests for ``sigantry sync apply --with-publish`` (Phase 17 / Plan 17-02).

Pins the operator-visible surface for the SYNC-PUBLISH compose flag added by
Plan 17-02. The flag wires through to ``apply_sync(with_publish=True, ...)``
(landed by Plan 17-01); this file covers the CLI-layer contract:

* ``--with-publish`` and ``--params`` are listed in ``sync apply --help``.
* ``--with-publish`` without ``--params`` raises ``typer.BadParameter`` whose
  message names the missing argument and points at runbook §1.3 + ADR-0013
  (D-17-01 / PUBLISH-04).
* ``--with-publish`` against a multi-env ``parameters.yml`` without
  ``--environment`` raises ``typer.BadParameter`` listing the available
  envs (D-17-02).
* ``--with-publish`` against an ``_ALL_``-only ``parameters.yml`` succeeds
  WITHOUT ``--environment`` (D-17-02 negative case).
* The default invocation (no ``--with-publish``) is byte-identical to v3.0:
  ``apply_sync`` is called with ``with_publish=False, params_path=None``
  and ``--params`` is NOT required (PUBLISH-01 SemVer-safety).
* The combined ``release_id`` (``sync-publish-<TS>``) is surfaced in stdout
  so operators can grep audit logs (D-17-04 visible-side).

The engine itself (``apply_sync``) is monkeypatched throughout -- this file
is concerned only with the CLI surface, not the orchestration. Engine-side
contracts are exhaustively covered in
``tests/sync/test_apply_with_publish.py`` (Plan 17-01).
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest
from typer.testing import CliRunner

from sigantry_core.sync import cli as sync_cli_mod
from sigantry_core.sync.apply import SyncApplyReport
from sigantry_core.sync.cli import sync_app

# click 8.3 removed the ``mix_stderr`` kwarg from ``CliRunner.__init__``;
# stderr is now combined into ``result.output`` for ``BadParameter`` flows by
# default. We assert against the combined stream so this file stays portable
# across click 8.x / 9.x.
runner = CliRunner()


def _bypass_preview_warning(monkeypatch: pytest.MonkeyPatch) -> None:
    """Acknowledge the preview-API gate so it doesn't pollute stdout."""
    monkeypatch.setenv("FDT_WORKFLOW__PREVIEW_APIS_ACKNOWLEDGED", "true")
    sync_cli_mod._PREVIEW_WARNING_EMITTED.clear()


def _make_report(
    *,
    workspace_id: str = "ws-x",
    manifest_path: str = "m.yml",
    items_packaged: int = 1,
    folders_created: int = 0,
    items_moved: int = 0,
    deploy_record_release_id: str = "sync-publish-2026-05-01T12-34-56Z",
    outcome: str = "succeeded",
    failure_reason: str | None = None,
    staging_dir: Path | None = None,
    publish_outcome: str | None = None,
    failed_item: str | None = None,
) -> SyncApplyReport:
    """Build a synthetic ``SyncApplyReport`` mirroring the Plan 17-01 shape."""
    return SyncApplyReport(
        workspace_id=workspace_id,
        manifest_path=manifest_path,
        items_packaged=items_packaged,
        folders_created=folders_created,
        items_moved=items_moved,
        deploy_record_release_id=deploy_record_release_id,
        outcome=outcome,
        failure_reason=failure_reason,
        staging_dir=staging_dir,
        publish_outcome=publish_outcome,
        failed_item=failed_item,
    )


def _stub_apply(monkeypatch: pytest.MonkeyPatch, report: SyncApplyReport) -> MagicMock:
    """Replace ``sigantry_core.sync.cli.apply_sync`` with a MagicMock returning ``report``."""
    mock = MagicMock(return_value=report)
    monkeypatch.setattr(sync_cli_mod, "apply_sync", mock)
    return mock


def _write_params_yaml(tmp_path: Path, *, multi_env: bool) -> Path:
    """Write a minimal ``parameters.yml`` fixture.

    ``multi_env=True`` -> three real environment labels (DEV / PREPROD / PROD).
    ``multi_env=False`` -> ``_ALL_`` wildcard only (``environments_seen == set()``).

    Schema mirrors the canonical shape validated by
    :func:`sigantry_core.deploy.parameters.load_and_validate`.
    """
    p = tmp_path / "p.yml"
    if multi_env:
        p.write_text(
            "find_replace:\n"
            "  - find_value: 'placeholder'\n"
            "    replace_value:\n"
            "      DEV: '$workspace.$id'\n"
            "      PREPROD: '$workspace.$id'\n"
            "      PROD: '$workspace.$id'\n",
            encoding="utf-8",
        )
    else:
        p.write_text(
            "find_replace:\n"
            "  - find_value: 'placeholder'\n"
            "    replace_value:\n"
            "      _ALL_: '$workspace.$id'\n",
            encoding="utf-8",
        )
    return p


# ---------------------------------------------------------------------------
# Test 1: --with-publish + --params present in help
# ---------------------------------------------------------------------------


def test_with_publish_flag_present_in_help() -> None:
    """``sigantry sync apply --help`` lists ``--with-publish`` and ``--params``.

    PUBLISH-01 surface visibility -- operators must be able to discover the
    flag without reading the runbook.
    """
    result = runner.invoke(sync_app, ["apply", "--help"])
    assert result.exit_code == 0, result.output
    assert "--with-publish" in result.output
    assert "--params" in result.output


# ---------------------------------------------------------------------------
# Test 2: --with-publish without --params raises BadParameter (D-17-01 / PUBLISH-04)
# ---------------------------------------------------------------------------


def test_with_publish_without_params_raises_bad_parameter(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``--with-publish`` without ``--params`` exits non-zero with a friendly hint.

    D-17-01 hard-fail rule. The error message MUST name the missing
    argument and reference the runbook (or apply.md) so operators can
    self-serve.
    """
    _bypass_preview_warning(monkeypatch)
    # Stub the engine so we can verify that even if the validator skipped,
    # the error would still surface (defence in depth).
    _stub_apply(monkeypatch, _make_report())

    manifest = tmp_path / "sync.yml"
    manifest.write_text("schema_version: '1.0.0'\nitems: []\n", encoding="utf-8")

    result = runner.invoke(
        sync_app,
        [
            "apply",
            "--manifest",
            str(manifest),
            "--workspace-id",
            "ws-x",
            "--with-publish",
        ],
    )
    assert result.exit_code != 0, result.output
    # click 8.3 combines stderr into result.output by default.
    assert "--with-publish requires --params" in result.output, result.output
    # Cite the runbook OR apply.md (operator self-serve).
    assert "runbook" in result.output.lower() or "apply.md" in result.output, result.output


# ---------------------------------------------------------------------------
# Test 3: --with-publish + --params + --environment forwards kwargs correctly
# ---------------------------------------------------------------------------


def test_with_publish_forwards_kwargs_to_apply_sync(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Successful ``--with-publish`` invocation forwards ``with_publish``,
    ``params_path``, and ``environment`` to ``apply_sync``.

    The CLI is a thin façade over the engine helper; this test pins the
    kwarg-forwarding contract so a future refactor cannot silently drop one.
    """
    _bypass_preview_warning(monkeypatch)
    params_yml = _write_params_yaml(tmp_path, multi_env=True)
    manifest = tmp_path / "sync.yml"
    manifest.write_text("schema_version: '1.0.0'\nitems: []\n", encoding="utf-8")

    mock_apply = _stub_apply(monkeypatch, _make_report())

    result = runner.invoke(
        sync_app,
        [
            "apply",
            "--manifest",
            str(manifest),
            "--workspace-id",
            "ws-x",
            "--with-publish",
            "--params",
            str(params_yml),
            "--environment",
            "DEV",
        ],
    )
    assert result.exit_code == 0, result.output
    mock_apply.assert_called_once()
    kwargs = mock_apply.call_args.kwargs
    assert kwargs.get("with_publish") is True
    assert str(kwargs.get("params_path")) == str(params_yml)
    assert kwargs.get("environment") == "DEV"


# ---------------------------------------------------------------------------
# Test 4: default invocation byte-identical to v3.0 (PUBLISH-01 SemVer-safety)
# ---------------------------------------------------------------------------


def test_default_invocation_byte_identical_to_v3_0(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No ``--with-publish`` -> ``apply_sync`` called with ``with_publish=False,
    params_path=None``. ``--params`` is NOT required.

    PUBLISH-01 SemVer-safety. The default-path operator never sees the
    new compose surface; their CLI behaviour is unchanged.
    """
    _bypass_preview_warning(monkeypatch)
    manifest = tmp_path / "sync.yml"
    manifest.write_text("schema_version: '1.0.0'\nitems: []\n", encoding="utf-8")
    mock_apply = _stub_apply(
        monkeypatch,
        _make_report(deploy_record_release_id="sync-2026-05-01T12-34-56Z"),
    )

    result = runner.invoke(
        sync_app,
        [
            "apply",
            "--manifest",
            str(manifest),
            "--workspace-id",
            "ws-x",
        ],
    )
    assert result.exit_code == 0, result.output
    mock_apply.assert_called_once()
    kwargs = mock_apply.call_args.kwargs
    assert kwargs.get("with_publish") is False
    assert kwargs.get("params_path") is None


# ---------------------------------------------------------------------------
# Test 5: --with-publish + multi-env params without --environment raises (D-17-02)
# ---------------------------------------------------------------------------


def test_with_publish_multi_env_params_without_environment_flag_raises(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Multi-env ``parameters.yml`` (DEV / PREPROD / PROD) requires ``--environment``.

    D-17-02 -- the CLI inspects ``ParametersConfig.environments_seen`` and
    refuses to invoke the engine without an environment label when the
    parameters file references more than just ``_ALL_``. The error message
    MUST name ``--environment`` AND list available envs so the operator
    can pick one without re-reading the YAML.
    """
    _bypass_preview_warning(monkeypatch)
    params_yml = _write_params_yaml(tmp_path, multi_env=True)
    manifest = tmp_path / "sync.yml"
    manifest.write_text("schema_version: '1.0.0'\nitems: []\n", encoding="utf-8")
    _stub_apply(monkeypatch, _make_report())

    result = runner.invoke(
        sync_app,
        [
            "apply",
            "--manifest",
            str(manifest),
            "--workspace-id",
            "ws-x",
            "--with-publish",
            "--params",
            str(params_yml),
        ],
    )
    assert result.exit_code != 0, result.output
    assert "--environment" in result.output, result.output
    # At least one of the known env labels appears so the operator knows
    # which to pick.
    assert any(label in result.output for label in ("DEV", "PREPROD", "PROD")), result.output


# ---------------------------------------------------------------------------
# Test 6: --with-publish + _ALL_-only params succeeds without --environment (D-17-02)
# ---------------------------------------------------------------------------


def test_with_publish_all_only_params_no_environment_required(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``_ALL_``-only ``parameters.yml`` -> ``--environment`` is OPTIONAL.

    D-17-02 negative case. ``ParametersConfig.environments_seen == frozenset()``
    is the green-light path -- the CLI invokes the engine with
    ``environment=None``.
    """
    _bypass_preview_warning(monkeypatch)
    params_yml = _write_params_yaml(tmp_path, multi_env=False)
    manifest = tmp_path / "sync.yml"
    manifest.write_text("schema_version: '1.0.0'\nitems: []\n", encoding="utf-8")
    mock_apply = _stub_apply(monkeypatch, _make_report())

    result = runner.invoke(
        sync_app,
        [
            "apply",
            "--manifest",
            str(manifest),
            "--workspace-id",
            "ws-x",
            "--with-publish",
            "--params",
            str(params_yml),
        ],
    )
    assert result.exit_code == 0, result.output
    mock_apply.assert_called_once()
    kwargs = mock_apply.call_args.kwargs
    assert kwargs.get("with_publish") is True
    assert kwargs.get("environment") is None


# ---------------------------------------------------------------------------
# Test 7: success-path stdout exposes the combined release_id (D-17-04 visible-side)
# ---------------------------------------------------------------------------


def test_with_publish_success_stdout_includes_release_id_marker(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Success stdout must include ``release_id=sync-publish-<TS>`` so operators
    can grep ``~/.sigantry/audit/deploys.jsonl`` without re-running the command.

    D-17-04 visible-side -- the unique release_id prefix is the operator's
    audit-trail anchor.
    """
    _bypass_preview_warning(monkeypatch)
    params_yml = _write_params_yaml(tmp_path, multi_env=False)
    manifest = tmp_path / "sync.yml"
    manifest.write_text("schema_version: '1.0.0'\nitems: []\n", encoding="utf-8")
    _stub_apply(
        monkeypatch,
        _make_report(deploy_record_release_id="sync-publish-2026-05-01T12-34-56Z"),
    )

    result = runner.invoke(
        sync_app,
        [
            "apply",
            "--manifest",
            str(manifest),
            "--workspace-id",
            "ws-x",
            "--with-publish",
            "--params",
            str(params_yml),
        ],
    )
    assert result.exit_code == 0, result.output
    # Rich's Console wraps long lines at terminal width; flatten and check
    # for the substring rather than the full ``release_id=...`` token.
    flat = result.output.replace("\n", "")
    assert "release_id=sync-publish-2026-05-01T12-34-56Z" in flat, flat


# ---------------------------------------------------------------------------
# Test 7: --republish-existing forwards republish_existing=True (D-17-10)
# ---------------------------------------------------------------------------


def test_republish_existing_forwards_kwarg_to_apply_sync(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``--with-publish --republish-existing`` forwards ``republish_existing=True``.

    Pins the kwarg-forwarding contract for the D-17-10 modifier so a
    future CLI refactor cannot silently drop it.
    """
    _bypass_preview_warning(monkeypatch)
    params_yml = _write_params_yaml(tmp_path, multi_env=False)
    manifest = tmp_path / "sync.yml"
    manifest.write_text("schema_version: '1.0.0'\nitems: []\n", encoding="utf-8")
    mock_apply = _stub_apply(monkeypatch, _make_report())

    result = runner.invoke(
        sync_app,
        [
            "apply",
            "--manifest",
            str(manifest),
            "--workspace-id",
            "ws-x",
            "--with-publish",
            "--republish-existing",
            "--params",
            str(params_yml),
        ],
    )
    assert result.exit_code == 0, result.output
    mock_apply.assert_called_once()
    kwargs = mock_apply.call_args.kwargs
    assert kwargs.get("with_publish") is True
    assert kwargs.get("republish_existing") is True


def test_republish_existing_default_false_forwarded(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Without ``--republish-existing`` the engine is called with
    ``republish_existing=False`` -- SemVer-safe default path."""
    _bypass_preview_warning(monkeypatch)
    params_yml = _write_params_yaml(tmp_path, multi_env=False)
    manifest = tmp_path / "sync.yml"
    manifest.write_text("schema_version: '1.0.0'\nitems: []\n", encoding="utf-8")
    mock_apply = _stub_apply(monkeypatch, _make_report())

    result = runner.invoke(
        sync_app,
        [
            "apply",
            "--manifest",
            str(manifest),
            "--workspace-id",
            "ws-x",
            "--with-publish",
            "--params",
            str(params_yml),
        ],
    )
    assert result.exit_code == 0, result.output
    assert mock_apply.call_args.kwargs.get("republish_existing") is False


# ---------------------------------------------------------------------------
# Test 8: --republish-existing without --with-publish raises (D-17-10 gate)
# ---------------------------------------------------------------------------


def test_republish_existing_without_with_publish_raises(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``--republish-existing`` alone exits non-zero naming ``--with-publish``.

    The modifier is meaningless without a publish to modify; the error
    must attribute to the flag the operator typed and stay self-serve.
    """
    _bypass_preview_warning(monkeypatch)
    # Stub the engine so a missed validator would otherwise succeed --
    # defence in depth (mirrors Test 2).
    _stub_apply(monkeypatch, _make_report())
    manifest = tmp_path / "sync.yml"
    manifest.write_text("schema_version: '1.0.0'\nitems: []\n", encoding="utf-8")

    result = runner.invoke(
        sync_app,
        [
            "apply",
            "--manifest",
            str(manifest),
            "--workspace-id",
            "ws-x",
            "--republish-existing",
        ],
    )
    assert result.exit_code != 0, result.output
    assert "--republish-existing requires --with-publish" in result.output, result.output


# ---------------------------------------------------------------------------
# Test 9: --republish-existing listed in --help
# ---------------------------------------------------------------------------


def test_republish_existing_flag_present_in_help() -> None:
    """``sigantry sync apply --help`` lists ``--republish-existing``."""
    result = runner.invoke(sync_app, ["apply", "--help"])
    assert result.exit_code == 0, result.output
    assert "--republish-existing" in result.output


# ---------------------------------------------------------------------------
# Test 10 (S-T1a): a failed publish exits NON-ZERO and names the outcome
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("publish_outcome", ["failed", "partial-failure"])
def test_with_publish_failed_publish_exits_non_zero(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    publish_outcome: str,
) -> None:
    """A ``--with-publish`` run whose publish half failed must exit non-zero.

    S-T1a regression guard. ``publish_absent_items`` RETURNS a
    ``PublishResult(outcome="failed"/"partial-failure")`` rather than
    raising (PUBLISH-05 keeps the folder reconcile committed), and the
    engine surfaces that as ``SyncApplyReport.publish_outcome`` while the
    reconcile-side ``outcome`` stays ``"succeeded"``. Before this fix the
    CLI inspected only ``outcome`` and printed ``sync apply succeeded`` /
    exit 0 for any non-dry-run report -- so a failed publish read as a
    green build. The CLI must now name the publish outcome and exit 1.

    Fail direction: with the pre-fix ``apply_cmd`` (no ``publish_outcome``
    check) this assertion fails because ``result.exit_code == 0``.
    """
    _bypass_preview_warning(monkeypatch)
    params_yml = _write_params_yaml(tmp_path, multi_env=False)
    manifest = tmp_path / "sync.yml"
    manifest.write_text("schema_version: '1.0.0'\nitems: []\n", encoding="utf-8")
    _stub_apply(
        monkeypatch,
        _make_report(
            deploy_record_release_id="sync-publish-2026-05-01T12-34-56Z",
            outcome="succeeded",  # reconcile committed (PUBLISH-05)
            publish_outcome=publish_outcome,
            failed_item="loader.Notebook",
        ),
    )

    result = runner.invoke(
        sync_app,
        [
            "apply",
            "--manifest",
            str(manifest),
            "--workspace-id",
            "ws-x",
            "--with-publish",
            "--params",
            str(params_yml),
        ],
    )
    assert result.exit_code == 1, result.output
    flat = result.output.replace("\n", "")
    assert f"publish {publish_outcome}" in flat, flat
    # The failing item is surfaced so the operator can triage without
    # re-reading the audit ledger.
    assert "loader.Notebook" in flat, flat


def test_with_publish_succeeded_publish_exits_zero(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A ``--with-publish`` run whose publish succeeded still exits 0.

    Guards the other direction of the S-T1a change: a clean publish
    (``publish_outcome="succeeded"``) must not be turned red by the new
    exit-code branch.
    """
    _bypass_preview_warning(monkeypatch)
    params_yml = _write_params_yaml(tmp_path, multi_env=False)
    manifest = tmp_path / "sync.yml"
    manifest.write_text("schema_version: '1.0.0'\nitems: []\n", encoding="utf-8")
    _stub_apply(
        monkeypatch,
        _make_report(
            deploy_record_release_id="sync-publish-2026-05-01T12-34-56Z",
            outcome="succeeded",
            publish_outcome="succeeded",
        ),
    )

    result = runner.invoke(
        sync_app,
        [
            "apply",
            "--manifest",
            str(manifest),
            "--workspace-id",
            "ws-x",
            "--with-publish",
            "--params",
            str(params_yml),
        ],
    )
    assert result.exit_code == 0, result.output
