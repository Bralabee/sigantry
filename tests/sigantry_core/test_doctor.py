"""Tests for the ``sigantry-core doctor`` CLI (PROD-18).

Covers the minimum-viable doctor contract per PRODUCTIZATION.md Section 10.2
row 1:

- Exit 0 when no plugins are registered (doctor is a diagnostic, not a gate).
- Lists HS2 plugins when the plugin package is installed.
- Surfaces per-plugin import errors in the table without aborting the process.
- ``--strict`` flips the exit code to 1 when any plugin failed to import.
- ``doctor --help`` exits 0.
- Table always carries the five column headers.
- Output ordering is deterministic (sort by group then name).
"""

from __future__ import annotations

import pytest
from typer.testing import CliRunner

from sigantry_core import doctor as doctor_module
from sigantry_core.cli import app
from sigantry_core.registry import PluginInfo, Registry

_EXPECTED_HEADERS = ("Group", "Name", "Module", "Version", "Status")


@pytest.fixture
def empty_registry() -> Registry:
    """Fresh registry with no plugins registered AND no entry-point discovery."""
    r = Registry()
    # Discovery is idempotent; mark it done without actually scanning env
    # entry points so the test stays hermetic.
    r._discovered = True
    return r


@pytest.fixture
def registry_with_error() -> Registry:
    """Registry whose ``list_plugins`` reports one healthy and one broken plugin."""
    r = Registry()
    r._discovered = True
    r._info["sigantry.telemetry_sinks"]["good"] = PluginInfo(
        group="sigantry.telemetry_sinks",
        name="good",
        module="example.telemetry.good",
        version="0.0.1",
    )
    r._info["sigantry.deploy_profiles"]["broken"] = PluginInfo(
        group="sigantry.deploy_profiles",
        name="broken",
        module="example.deploy.broken",
        import_error="RuntimeError: boom",
    )
    return r


@pytest.fixture(autouse=True)
def _reset_doctor_override():
    """Ensure every test starts with a clean override and restores afterwards."""
    doctor_module._set_registry_override(None)
    yield
    doctor_module._set_registry_override(None)


def _invoke_doctor(*args: str) -> object:
    # Newer ``typer.testing.CliRunner`` (backed by Click 8.2+) no longer
    # accepts ``mix_stderr`` — stderr is separated by default. The result
    # object exposes ``.stdout`` plus ``.stderr`` when applicable.
    runner = CliRunner()
    return runner.invoke(app, ["doctor", *args])


def test_doctor_exits_zero_with_no_plugins(empty_registry: Registry) -> None:
    doctor_module._set_registry_override(empty_registry)
    result = _invoke_doctor()
    assert result.exit_code == 0, f"stderr: {result.stderr}"
    assert "0 plugin(s) discovered" in result.stdout


def test_doctor_lists_hs2_plugins_after_install() -> None:
    """With the HS2 plugin installed (editable), doctor lists all six seams."""
    # This test intentionally uses the LIVE default registry: the plugin is
    # expected to be installed in the dev venv per the plan's setup. Renamed
    # from fabric_dataops_toolkits_hs2 in v3.0 per ADR-0011.
    pytest.importorskip("sigantry_hs2")
    result = _invoke_doctor()
    assert result.exit_code == 0
    out = result.stdout
    for plugin_name in ("aims", "dq_framework", "log_analytics", "hs2_entra_group"):
        assert plugin_name in out, f"doctor output missing plugin {plugin_name!r}; got:\n{out}"


def test_doctor_reports_import_error(registry_with_error: Registry) -> None:
    doctor_module._set_registry_override(registry_with_error)
    result = _invoke_doctor()
    assert result.exit_code == 0  # not strict: non-zero is opt-in
    # rich may wrap long cells; search for either "RuntimeError" or "boom" token.
    combined = result.stdout
    assert "error:" in combined
    assert "boom" in combined or "RuntimeError" in combined


def test_doctor_strict_exits_one_on_import_error(registry_with_error: Registry) -> None:
    doctor_module._set_registry_override(registry_with_error)
    result = _invoke_doctor("--strict")
    assert result.exit_code == 1, f"stdout: {result.stdout}, stderr: {result.stderr}"


def test_doctor_help_exits_zero() -> None:
    result = _invoke_doctor("--help")
    assert result.exit_code == 0
    assert "doctor" in result.stdout.lower() or "doctor" in result.stdout


def test_doctor_table_columns_present(registry_with_error: Registry) -> None:
    doctor_module._set_registry_override(registry_with_error)
    result = _invoke_doctor()
    assert result.exit_code == 0
    for header in _EXPECTED_HEADERS:
        assert header in result.stdout, (
            f"doctor table missing column header {header!r}; got:\n{result.stdout}"
        )


def test_doctor_deterministic_ordering(registry_with_error: Registry) -> None:
    """Two invocations produce identical row ordering (sort by group, then name)."""
    doctor_module._set_registry_override(registry_with_error)
    first = _invoke_doctor()
    second = _invoke_doctor()
    assert first.exit_code == 0
    assert second.exit_code == 0
    assert first.stdout == second.stdout, (
        "doctor output must be byte-identical across invocations for stable "
        "CI comparisons; observed drift."
    )
    # Sanity-check: the "deploy_profiles / broken" row appears BEFORE the
    # "telemetry_sinks / good" row because (deploy < telemetry).
    broken_idx = first.stdout.find("broken")
    good_idx = first.stdout.find("good")
    assert -1 < broken_idx < good_idx, (
        "doctor rows must be sorted by (group, name); observed order: "
        f"broken at {broken_idx}, good at {good_idx}"
    )


def test_doctor_footer_reports_plugin_count(empty_registry: Registry) -> None:
    """The footer always names the number of groups even when no plugins exist.

    The count comes from :meth:`Registry.known_new_groups` (the v3.0 entry-point
    surface). After Phase 16 the active set is 11 groups
    (work_item_providers + capacity_policies + log_sinks + deploy_profiles +
    pipeline_gates + runbook_registries + token_providers + change_request_providers
    + notification_sinks + secret_stores + approval_gates).
    """
    doctor_module._set_registry_override(empty_registry)
    result = _invoke_doctor()
    assert result.exit_code == 0
    expected_count = len(Registry.known_new_groups())
    assert f"{expected_count} seam group" in result.stdout
