"""Unit tests for sigantry_core.workspace.blueprints."""

from __future__ import annotations

import pytest

from sigantry_core.workspace.blueprints import BLUEPRINTS, get_blueprint


def test_minimal_starter_blueprint_present() -> None:
    assert "minimal_starter" in BLUEPRINTS


def test_medallion_alias_matches_minimal_starter() -> None:
    """`medallion` is an explicit alias for the same canonical layout."""
    assert BLUEPRINTS["medallion"] == BLUEPRINTS["minimal_starter"]


def test_minimal_starter_preserves_pipeline_flow_order() -> None:
    """Order matters -- Fabric UI lists folders top-to-bottom in this order."""
    folders = BLUEPRINTS["minimal_starter"]
    assert folders == (
        "00_control",
        "10_intake",
        "20_storage",
        "30_transform",
        "40_semantic",
        "50_reporting",
        "90_shared",
        "99_retired",
    )
    # The prefixes exist so name-sorting (the Fabric UI's order) is flow order.
    assert list(folders) == sorted(folders)


def test_get_blueprint_returns_tuple() -> None:
    """Blueprint values are tuples (immutable to prevent operator mutation)."""
    assert isinstance(get_blueprint("minimal_starter"), tuple)


def test_get_blueprint_unknown_lists_available() -> None:
    """Error message must surface the full catalog so the operator can self-correct."""
    with pytest.raises(KeyError, match="medallion") as exc:
        get_blueprint("not_a_real_blueprint")
    assert "minimal_starter" in str(exc.value)
