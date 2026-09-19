"""Contract test (D-08): committed JSON Schema equals ``SyncManifest.model_json_schema()``.

A pydantic-model change that adds, removes, or renames a field will
flip ``test_committed_sync_schema_equals_runtime`` red. The contributor
must either:

  (a) regenerate ``docs/reference/sync-schema.json`` from the runtime
      output (intentional change; requires a SemVer-minor
      ``schema_version`` bump in :class:`SyncManifest` per D-05), OR

  (b) revert the offending model change.

The committed schema is the IDE-autocomplete + JSON-schema-validator
surface for ``sync.yml``; this test is the drift catcher.

The companion keyset assertion
(``test_committed_sync_schema_top_level_keyset``) is a softer-grade
SemVer contract that survives intentional regeneration but would fire
on a structural shape change (e.g. pydantic v3 dropping ``$defs``).
"""

from __future__ import annotations

import json
from pathlib import Path

from sigantry_core.sync.manifest import SyncManifest

REPO_ROOT = Path(__file__).resolve().parents[2]
COMMITTED = REPO_ROOT / "docs" / "reference" / "sync-schema.json"


def test_committed_sync_schema_equals_runtime() -> None:
    """Canonicalised committed schema must equal ``model_json_schema()`` output."""
    committed = COMMITTED.read_text(encoding="utf-8")
    runtime_obj = SyncManifest.model_json_schema()
    runtime = json.dumps(runtime_obj, indent=2, sort_keys=True) + "\n"
    if committed != runtime:
        raise AssertionError(
            "Committed sync-schema.json drifted from "
            "SyncManifest.model_json_schema(). Either regenerate the file "
            "(see 13-01-PLAN.md Task 2 recipe -- "
            'python -c "import json; from pathlib import Path; '
            "from sigantry_core.sync.manifest import SyncManifest; "
            "Path('docs/reference/sync-schema.json').write_text("
            "json.dumps(SyncManifest.model_json_schema(), indent=2, sort_keys=True) + '\\n', "
            "encoding='utf-8')\") and bump schema_version per D-05, "
            "or revert the model change.\n\n"
            f"Committed bytes: {len(committed)}, runtime bytes: {len(runtime)}"
        )


def test_committed_sync_schema_top_level_keyset() -> None:
    """SemVer-keyset contract -- top-level pydantic JSON Schema shape."""
    schema = json.loads(COMMITTED.read_text(encoding="utf-8"))
    # pydantic v2 emits ``properties``, ``$defs`` (or ``definitions`` in
    # legacy mode), ``type``, ``required``. The keyset is asserted softly:
    # ``properties`` and ``$defs``/``definitions`` are load-bearing for
    # IDE autocomplete + JSON-schema validators.
    assert "properties" in schema
    assert "$defs" in schema or "definitions" in schema
    assert "schema_version" in schema["properties"]
    assert "items" in schema["properties"]
    assert "folders" in schema["properties"]


def test_committed_sync_schema_is_valid_json() -> None:
    """Committed file parses as JSON (catches accidental hand-edits that break syntax)."""
    json.loads(COMMITTED.read_text(encoding="utf-8"))


def test_committed_sync_schema_ends_with_single_newline() -> None:
    """Canonicalisation contract: file ends with exactly one trailing LF."""
    content = COMMITTED.read_bytes()
    assert content.endswith(b"\n"), "committed schema must end with newline"
    assert not content.endswith(b"\n\n"), "committed schema must end with a SINGLE trailing newline"
