"""``sync apply``, ``dq gate``, ``workspace bootstrap`` and ``doctor`` print data as written.

Rich reads ``[...]`` in a string it prints as markup. These commands put
manifest values, option values, paths and exception text into such strings,
so a value holding a closing tag such as ``[/old]`` raised ``MarkupError``
while the command printed its message -- a traceback and exit 1 in place of
the message and the documented exit code -- and a tag-like word such as
``[draft]`` was dropped (issue #80).

Every case runs offline: each command fails, or is stubbed, before any request
to Fabric. The static check in ``test_cli_markup_safety.py`` covers the other
print sites.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from sigantry_core import doctor as doctor_mod
from sigantry_core.cli import app
from sigantry_core.registry import PluginInfo, Registry
from sigantry_core.sync import cli as sync_cli_mod

runner = CliRunner()


@pytest.fixture(autouse=True)
def _offline_cwd(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    home = tmp_path / "home"
    temp = tmp_path / "tmp"
    home.mkdir()
    temp.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("TMPDIR", str(temp))
    monkeypatch.setenv("SIGANTRY_WORKFLOW__PREVIEW_APIS_ACKNOWLEDGED", "true")
    # Wide enough that Rich never wraps a value inside a table cell.
    monkeypatch.setenv("COLUMNS", "200")
    sync_cli_mod._PREVIEW_WARNING_EMITTED.clear()
    monkeypatch.chdir(tmp_path)


def _flat(text: str) -> str:
    """Rich wraps long lines at the console width; compare word by word."""
    return " ".join(text.split())


@pytest.mark.parametrize("display_name", ["Sales [/old] v1.2", "Q3 report [draft] v1.2"])
def test_sync_apply_validation_error_prints_display_name_as_written(
    tmp_path: Path, display_name: str
) -> None:
    (tmp_path / "nb").mkdir()
    (tmp_path / "sync.yml").write_text(
        "schema_version: '1.0.0'\n"
        "items:\n"
        "  - local_path: nb\n"
        "    type: Notebook\n"
        f"    display_name: {display_name}\n",
        encoding="utf-8",
    )
    result = runner.invoke(
        app,
        ["sync", "apply", "--workspace-id", "ws-x", "--manifest", "sync.yml", "--dry-run"],
    )
    # Exit 1 is also what a MarkupError traceback gives, so the message decides.
    assert result.exit_code == 1, repr(result.exception)
    assert result.exception is None or isinstance(result.exception, SystemExit)
    flat = _flat(result.stdout)
    assert "Manifest validation failed:" in flat
    assert f"display_name {display_name!r}: banned characters: ['.']" in flat


def test_dq_gate_unknown_gate_name_is_a_config_error() -> None:
    result = runner.invoke(
        app, ["dq", "gate", "--suite", "s", "--dataset", "d", "--gate", "nightly[/x]"]
    )
    assert result.exit_code == 2, repr(result.exception)
    flat = _flat(result.stdout)
    assert "DQ gate config error:" in flat
    assert "No plugin named 'nightly[/x]'" in flat


def test_workspace_bootstrap_missing_config_is_exit_two() -> None:
    result = runner.invoke(app, ["workspace", "bootstrap", "cfg[/x]/workspace.yml", "--dry-run"])
    assert result.exit_code == 2, repr(result.exception)
    assert "ERROR workspace.yml not found at cfg[/x]/workspace.yml" in _flat(result.stdout)


class _BrokenPluginRegistry(Registry):
    """One plugin whose import failed with a message holding markup-like text."""

    def discover(self) -> None:
        return None

    def list_plugins(self, group: str | None = None) -> list[PluginInfo]:
        return [
            PluginInfo(
                group="sigantry.dq_gates",
                name="gate[/old]",
                module="pkg.gate",
                import_error="No module named 'pkg[draft]'",
            )
        ]


def test_doctor_prints_plugin_name_and_import_error_as_written() -> None:
    doctor_mod._set_registry_override(_BrokenPluginRegistry())
    try:
        result = runner.invoke(app, ["doctor", "--strict"])
    finally:
        doctor_mod._set_registry_override(None)
    assert result.exit_code == 1, repr(result.exception)
    assert result.exception is None or isinstance(result.exception, SystemExit)
    assert "gate[/old]" in result.stdout
    assert "error: No module named 'pkg[draft]'" in result.stdout
