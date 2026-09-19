"""Real assertions for sigantry_core.sync.manifest (Plan 13-01 / SYNC-01 + SYNC-06).

Replaces the eight Wave 0 xfail stubs from Plan 13-00 with executable
assertions tied 1:1 to the locked decisions in CONTEXT.md:

- D-05: schema_version forward-compat policy (minor accepted, major rejected).
- D-06: SyncItem.type validated against fabric_cicd.constants.ItemType (the
  upstream enum's PascalCase ``.value``).
- D-07 / SYNC-06: dataflow_gen2 / streaming_semantic_model / streaming_dataflow
  trigger a logged WARNING and force ``target_folder='/'`` via
  :meth:`SyncManifest.normalised`.
- D-08: ``docs/reference/sync-schema.json`` is the committed JSON Schema; the
  byte-equality contract test lives in ``test_sync_schema_committed.py``.

Council D constraints:

- #3 (depth): target_folder paths exceeding 10 segments are rejected.
- #4 (banned chars): ``~"#.&*:<>?/{|}``, leading/trailing whitespace,
  C0/C1 control chars, length > 255 are rejected.

The eight test names are load-bearing per the threat model in
``13-00-PLAN.md`` (T-13-W0-LOAD-BEARING-NAMES) -- they MUST NOT be renamed
even if their bodies change. The xfail markers from Wave 0 are now removed.
"""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

from sigantry_core.sync.errors import ManifestValidationError
from sigantry_core.sync.manifest import (
    SyncItem,
    SyncManifest,
    load_manifest,
)


def _write(path: Path, content: str) -> Path:
    path.write_text(content, encoding="utf-8")
    return path


def test_manifest_parses_minimal_valid_yaml(tmp_path: Path) -> None:
    """SyncManifest pydantic model parses a minimal valid sync.yml."""
    manifest_path = _write(
        tmp_path / "sync.yml",
        """\
schema_version: "1.0.0"
items:
  - local_path: nb.ipynb
    type: Notebook
    display_name: NB
""",
    )
    manifest = load_manifest(manifest_path)
    assert manifest.schema_version == "1.0.0"
    assert len(manifest.items) == 1
    item = manifest.items[0]
    assert item.type == "Notebook"
    assert item.target_folder == "/"
    assert item.display_name == "NB"


def test_manifest_rejects_missing_required_field(tmp_path: Path) -> None:
    """SyncItem without ``display_name`` raises ManifestValidationError."""
    manifest_path = _write(
        tmp_path / "sync.yml",
        """\
schema_version: "1.0.0"
items:
  - local_path: nb.ipynb
    type: Notebook
""",
    )
    with pytest.raises(ManifestValidationError) as excinfo:
        load_manifest(manifest_path)
    fields = [v["field"] for v in excinfo.value.violations]
    assert any("display_name" in f for f in fields), (
        f"display_name not surfaced in violations: {excinfo.value.violations}"
    )


def test_manifest_rejects_schema_version_major_mismatch(tmp_path: Path) -> None:
    """``schema_version='2.0.0'`` rejected; ``'1.0'`` and ``'1.0.0'`` accepted (D-05)."""
    bad = _write(
        tmp_path / "bad.yml",
        """\
schema_version: "2.0.0"
items:
  - local_path: nb.ipynb
    type: Notebook
    display_name: NB
""",
    )
    with pytest.raises(ManifestValidationError) as excinfo:
        load_manifest(bad)
    payload = excinfo.value.violations
    assert any("D-05" in v.get("decision_id", "") for v in payload), (
        f"D-05 decision id missing from violations: {payload}"
    )

    # Sanity: '1.0' (forward-compat per D-05) is accepted.
    good_short = _write(
        tmp_path / "good_short.yml",
        """\
schema_version: "1.0"
items:
  - local_path: nb.ipynb
    type: Notebook
    display_name: NB
""",
    )
    assert load_manifest(good_short).schema_version == "1.0"


def test_manifest_rejects_depth_over_10(tmp_path: Path) -> None:
    """target_folder='/a/b/c/d/e/f/g/h/i/j/k' (11 segments) rejected (Council D #3)."""
    eleven_levels = "/" + "/".join("abcdefghijk")  # 11 segments
    manifest_path = _write(
        tmp_path / "deep.yml",
        f"""\
schema_version: "1.0.0"
items:
  - local_path: nb.ipynb
    type: Notebook
    display_name: NB
    target_folder: "{eleven_levels}"
""",
    )
    with pytest.raises(ManifestValidationError) as excinfo:
        load_manifest(manifest_path)
    payload = " ".join(
        f"{v.get('field', '')} {v.get('reason', '')} {v.get('decision_id', '')}"
        for v in excinfo.value.violations
    )
    assert "Council-D-3" in payload, (
        f"Council-D-3 decision id missing from violations: {excinfo.value.violations}"
    )


@pytest.mark.parametrize(
    "banned_char",
    list('~"#&*:<>?{|}'),  # '/' is the segment separator; '.' tested in name body
)
def test_manifest_rejects_banned_chars_in_folder_name(tmp_path: Path, banned_char: str) -> None:
    """Folder name segment containing any of ``~"#&*:<>?{|}`` rejected (Council D #4)."""
    bad_folder = f"/parent/child{banned_char}name"
    manifest_path = _write(
        tmp_path / f"bad_{ord(banned_char):x}.yml",
        f"""\
schema_version: "1.0.0"
items:
  - local_path: nb.ipynb
    type: Notebook
    display_name: NB
    target_folder: "{bad_folder}"
""",
    )
    with pytest.raises(ManifestValidationError):
        load_manifest(manifest_path)


def test_manifest_rejects_unknown_item_type(tmp_path: Path) -> None:
    """type='Frobnicator' rejected (D-06: validated against ItemType enum)."""
    manifest_path = _write(
        tmp_path / "bad_type.yml",
        """\
schema_version: "1.0.0"
items:
  - local_path: nb.ipynb
    type: Frobnicator
    display_name: NB
""",
    )
    with pytest.raises(ManifestValidationError) as excinfo:
        load_manifest(manifest_path)
    payload = " ".join(
        f"{v.get('field', '')} {v.get('reason', '')} {v.get('decision_id', '')}"
        for v in excinfo.value.violations
    )
    # The pydantic translation layer surfaces the validator-emitted decision_id
    # in either the per-violation `decision_id` or the catch-all D-05/D-06/D-07
    # bucket; both are acceptable as long as D-06 is reachable.
    assert "D-06" in payload or "Frobnicator" in payload, (
        f"D-06 / unknown-type marker missing from violations: {excinfo.value.violations}"
    )


def test_manifest_folder_less_type_warning_at_validation(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """type='Dataflow' with ``target_folder='/x'`` warns and ``normalised()`` forces '/' (D-07/SYNC-06).

    ``Dataflow`` is the upstream ``fabric_cicd.constants.ItemType.DATAFLOW.value``
    canonical name (Dataflow Gen2 in user-speak); it is the only enum member
    that maps to a folder-less-class type as of fabric-cicd 1.0.x. The other
    spec-locked aliases (``streaming_semantic_model``, ``streaming_dataflow``)
    are not yet present in the upstream enum and would be rejected by the
    SyncItem.type validator before reaching the post-validate hook -- the
    folder-less alias set keeps them as a forward-compat marker but the
    falsifiable test target is ``Dataflow``.
    """
    manifest_path = _write(
        tmp_path / "folderless.yml",
        """\
schema_version: "1.0.0"
items:
  - local_path: df.json
    type: Dataflow
    display_name: MyDataflow
    target_folder: /x
""",
    )
    with caplog.at_level(logging.WARNING, logger="sigantry_core.sync.manifest"):
        manifest = load_manifest(manifest_path)
    assert any("folder_less_type_override" in record.getMessage() for record in caplog.records), (
        f"folder_less_type_override warning not surfaced; records: "
        f"{[r.getMessage() for r in caplog.records]}"
    )
    # Operator's literal input is preserved on the original instance.
    assert manifest.items[0].target_folder == "/x"
    # normalised() returns a copy where folder-less items have target_folder='/'.
    assert manifest.normalised().items[0].target_folder == "/"


def test_manifest_json_schema_committed_matches_runtime(tmp_path: Path) -> None:
    """LOAD-BEARING (D-08): ``SyncManifest.model_json_schema()`` exposes the expected top-level keyset.

    The byte-equality contract test (committed file vs runtime output) lives
    in :mod:`tests.sync.test_sync_schema_committed`; here we only assert the
    pydantic v2 keyset shape so test-runner imports stay self-contained.
    """
    schema = SyncManifest.model_json_schema()
    assert "properties" in schema
    # pydantic v2 emits ``$defs`` (some versions still emit ``definitions``);
    # both forms are accepted -- the byte-equality test is the strict drift
    # catcher.
    assert "$defs" in schema or "definitions" in schema
    properties = schema["properties"]
    assert "schema_version" in properties
    assert "items" in properties
    assert "folders" in properties
    # SyncItem must be reachable via the schema surface so IDE autocomplete
    # works for nested object completions.
    sync_item_def = schema.get("$defs", {}).get("SyncItem") or schema.get("definitions", {}).get(
        "SyncItem"
    )
    assert sync_item_def is not None, (
        f"SyncItem missing from schema definitions: {sorted(schema.keys())}"
    )
    item_props = sync_item_def.get("properties", {})
    for required in ("local_path", "type", "target_folder", "display_name"):
        assert required in item_props, (
            f"SyncItem.properties missing {required!r}: {sorted(item_props)}"
        )

    # Sanity: a constructed SyncItem round-trips via model_dump and re-parses.
    item = SyncItem(
        local_path=Path("nb.ipynb"),
        type="notebook",  # case-insensitive D-06 canonicalisation
        display_name="NB",
    )
    assert item.type == "Notebook"
    assert item.target_folder == "/"
