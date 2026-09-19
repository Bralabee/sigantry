"""Contract tests for ``docs/reference/drift-schema.json`` (Plan 13-06 / D-37).

The committed JSON Schema is the SemVer-pinned wire contract for
``sigantry diff --output json``. These tests are the drift-catcher:
any change to :class:`sigantry_core.sync.diff.DriftReport` that violates
the committed schema fails CI here.
"""

from __future__ import annotations

import json
from pathlib import Path

import jsonschema

from sigantry_core.sync.diff import DriftReport

REPO_ROOT = Path(__file__).resolve().parents[2]
SCHEMA_PATH = REPO_ROOT / "docs" / "reference" / "drift-schema.json"


def _load_committed_schema() -> dict:
    return json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))


def test_committed_drift_schema_top_level_keyset() -> None:
    """The committed JSON Schema's top-level required-keyset MUST match D-23.

    Required: sorted ``["added", "modified", "removed", "schema_version", "unchanged"]``;
    ``additionalProperties`` MUST be ``false`` so any new top-level key
    requires a coordinated v1.x.y -> v1.(x+1).0 schema bump.
    """
    schema = _load_committed_schema()
    assert sorted(schema["required"]) == sorted(
        [
            "added",
            "modified",
            "removed",
            "schema_version",
            "unchanged",
        ]
    )
    assert schema["additionalProperties"] is False
    # schema_version is locked to "1.0.0" via JSON Schema const.
    assert schema["properties"]["schema_version"]["const"] == "1.0.0"


def test_committed_drift_schema_per_entry_keyset() -> None:
    """Per-entry keysets locked per D-23.

    * ``added`` / ``removed``: ``{logical_id, display_name, type, folder_path}``
    * ``modified``: ``{logical_id, fields_changed}``
    * ``unchanged``: ``{logical_id}``

    Every per-entry sub-schema sets ``additionalProperties: false`` so an
    accidental new field on the runtime model is caught by the contract
    test below; here we just confirm the committed shape.
    """
    schema = _load_committed_schema()
    for bucket in ("added", "removed"):
        sub = schema["properties"][bucket]["items"]
        assert sorted(sub["required"]) == sorted(
            ["display_name", "folder_path", "logical_id", "type"]
        )
        assert sub["additionalProperties"] is False

    sub_mod = schema["properties"]["modified"]["items"]
    assert sorted(sub_mod["required"]) == sorted(["fields_changed", "logical_id"])
    assert sub_mod["additionalProperties"] is False

    sub_unc = schema["properties"]["unchanged"]["items"]
    assert sorted(sub_unc["required"]) == ["logical_id"]
    assert sub_unc["additionalProperties"] is False


def test_committed_drift_schema_equals_runtime() -> None:
    """LOAD-BEARING (D-37): runtime emission validates against the committed schema.

    Build a synthetic :class:`DriftReport` with one entry per category,
    serialise via ``DriftReport.to_json()``, and validate against the
    committed JSON Schema. Any future change to the runtime model that
    violates the schema (extra field, missing required field, wrong
    type) raises ``jsonschema.ValidationError`` here -- which is the
    SemVer-bump signal the test exists to catch.
    """
    schema = _load_committed_schema()
    report = DriftReport(
        schema_version="1.0.0",
        added=[
            {
                "logical_id": "a",
                "display_name": "A",
                "type": "Notebook",
                "folder_path": "/raw",
            }
        ],
        removed=[
            {
                "logical_id": "b",
                "display_name": "B",
                "type": "Notebook",
                "folder_path": "/raw/Bronze",
            }
        ],
        modified=[{"logical_id": "c", "fields_changed": ["folder_path", "type"]}],
        unchanged=[{"logical_id": "d"}],
    )
    payload = report.to_json()
    # Raises jsonschema.ValidationError on any drift; that is exactly
    # the failure mode the D-37 contract test exists to surface.
    jsonschema.validate(instance=payload, schema=schema)
