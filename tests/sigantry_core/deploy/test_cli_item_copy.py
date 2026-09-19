"""Unit tests for fabric-dataops fabric-item copy subcommand (DEPLOY-02)."""

from __future__ import annotations

from unittest.mock import patch

from typer.testing import CliRunner

from sigantry_core.cli import app
from sigantry_core.deploy.item_copy import ItemCopyError


def test_fabric_item_help() -> None:
    r = CliRunner().invoke(app, ["fabric-item", "--help"])
    assert r.exit_code == 0
    assert "copy" in r.stdout


def test_fabric_item_copy_happy_path() -> None:
    with patch(
        "sigantry_core.deploy.cli.copy_item",
        return_value="deadbeef-0000-4000-8000-000000000000",
    ) as m:
        r = CliRunner().invoke(
            app,
            [
                "fabric-item",
                "copy",
                "src/Bronze.Lakehouse",
                "dst/Silver.Lakehouse",
                "--new-display-name",
                "Silver",
            ],
        )
    assert r.exit_code == 0, r.stdout
    m.assert_called_once()
    kwargs = m.call_args.kwargs
    assert kwargs["new_display_name"] == "Silver"
    assert "deadbeef-0000-4000-8000-000000000000" in r.stdout


def test_fabric_item_copy_passes_description() -> None:
    with patch(
        "sigantry_core.deploy.cli.copy_item",
        return_value="abcd1234-0000-4000-8000-000000000000",
    ) as m:
        r = CliRunner().invoke(
            app,
            [
                "fabric-item",
                "copy",
                "src",
                "dst",
                "--new-display-name",
                "Silver",
                "--new-description",
                "silver zone",
            ],
        )
    assert r.exit_code == 0, r.stdout
    kwargs = m.call_args.kwargs
    assert kwargs["new_description"] == "silver zone"


def test_fabric_item_copy_error_exits_nonzero() -> None:
    with patch(
        "sigantry_core.deploy.cli.copy_item",
        side_effect=ItemCopyError("destination exists"),
    ):
        r = CliRunner().invoke(
            app,
            ["fabric-item", "copy", "src", "dst", "--new-display-name", "X"],
        )
    assert r.exit_code != 0
