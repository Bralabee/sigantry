"""The shipped ``templates/workspace.example.yml`` validates clean.

Parity guard for the greenfield bootstrap manifest, mirroring
``tests/starter/test_parameters_yml_validates.py`` (which guards the
parameters.yml example). Ensures the copy-and-edit workspace manifest we
ship stays loadable against ``WORKSPACE_SCHEMA`` if the schema evolves --
so the example can't silently rot out of sync with the loader.

Unlike parameters.yml, workspace.yml requires a LITERAL capacity GUID (the
schema enforces the GUID pattern and does not accept $ENV forms), so the
example needs no environment setup to validate.
"""

from __future__ import annotations

from pathlib import Path

_EXAMPLE = Path(__file__).resolve().parents[2] / "templates" / "workspace.example.yml"


def test_workspace_example_yml_validates_clean() -> None:
    """load_and_validate(templates/workspace.example.yml) parses with no exception."""
    from sigantry_core.workspace.bootstrap import load_and_validate

    cfg = load_and_validate(_EXAMPLE)

    assert cfg.path == str(_EXAMPLE)
    assert cfg.workspace_name, "example must set a workspace name"
    # Ships with the minimal_starter blueprint -> the 8-folder medallion layout.
    assert cfg.blueprint == "minimal_starter"
    assert cfg.folder_list[0] == "000 Orchestrate"
    assert len(cfg.folder_list) == 8
    # Ships Git-disabled (no repo assumed at bootstrap time).
    assert cfg.git_enabled is False
    # Default stage is the no-marker NONE.
    assert cfg.stage == "NONE"
