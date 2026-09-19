"""Plan 13-03 Task 2 -- GenericPackager + PACKAGER_REGISTRY (SYNC-03 / D-03 / D-12).

Replaces the Wave 0 xfail stubs from Plan 13-00 with real assertions.
The 5 load-bearing test names below MUST stay byte-stable -- the
T-13-W0-LOAD-BEARING-NAMES invariant pins them as the contract surface
that Plan 13-00 reserved for this plan.
"""

from __future__ import annotations

import inspect
import json
import re
import shutil
from pathlib import Path

from sigantry_core.sync.packagers import (
    PACKAGER_REGISTRY,
    GenericPackager,
    NotebookPackager,
)
from sigantry_core.sync.protocols import Packager

_UUID4_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$")

_FIXTURES = Path(__file__).resolve().parent / "fixtures"


# --------------------------------------------------------------------------
# Load-bearing test names (T-13-W0-LOAD-BEARING-NAMES) -- DO NOT RENAME
# --------------------------------------------------------------------------


def test_generic_packager_round_trip_data_pipeline(tmp_path) -> None:
    """fixture pipeline-content.json + stock template -> staged DataPipeline tree (SYNC-03)."""
    src_dir = tmp_path / "source"
    src_dir.mkdir()
    fixture = _FIXTURES / "data_pipeline" / "pipeline-content.json"
    shutil.copy2(fixture, src_dir / "pipeline-content.json")
    staging = tmp_path / "staging"

    pkg = GenericPackager(item_type="DataPipeline")
    out = pkg.pack(
        source=src_dir,
        display_name="MyPipeline",
        target_folder="/raw",
        logical_id=None,
        staging_dir=staging,
    )

    assert out == (staging / "raw" / "MyPipeline.DataPipeline").resolve()
    assert (out / ".platform").is_file()
    assert (out / "pipeline-content.json").is_file()

    payload = json.loads((out / ".platform").read_text(encoding="utf-8"))
    assert payload["metadata"]["type"] == "DataPipeline"
    assert payload["metadata"]["displayName"] == "MyPipeline"
    assert payload["config"]["version"] == "2.0"
    assert _UUID4_RE.match(payload["config"]["logicalId"])

    # Content payload preserved.
    content = json.loads((out / "pipeline-content.json").read_text(encoding="utf-8"))
    assert content == {"properties": {"activities": []}}


def test_generic_packager_round_trip_semantic_model(tmp_path) -> None:
    """fixture .tmdl tree + stock template -> staged SemanticModel tree (SYNC-03)."""
    src_dir = tmp_path / "source"
    (src_dir / "definition" / "cultures").mkdir(parents=True)
    (src_dir / "model.tmdl").write_text(
        "model MySemanticModel\n  defaultCulture: en-US\n",
        encoding="utf-8",
    )
    (src_dir / "definition" / "cultures" / "en-us.tmdl").write_text(
        "culture en-US\n",
        encoding="utf-8",
    )
    staging = tmp_path / "staging"

    pkg = GenericPackager(item_type="SemanticModel")
    out = pkg.pack(
        source=src_dir,
        display_name="MyModel",
        target_folder="/",
        logical_id=None,
        staging_dir=staging,
    )

    assert out == (staging / "MyModel.SemanticModel").resolve()
    assert (out / "model.tmdl").is_file()
    assert (out / "definition" / "cultures" / "en-us.tmdl").is_file()
    payload = json.loads((out / ".platform").read_text(encoding="utf-8"))
    assert payload["metadata"]["type"] == "SemanticModel"
    assert payload["metadata"]["displayName"] == "MyModel"


def test_generic_packager_persists_logical_id_per_type(tmp_path) -> None:
    """LOAD-BEARING (D-12): <source-dir>/.sigantry/<type>-ids.json keyed by relative-path."""
    src_dir = tmp_path / "source"
    src_dir.mkdir()
    fixture = _FIXTURES / "data_pipeline" / "pipeline-content.json"
    shutil.copy2(fixture, src_dir / "pipeline-content.json")

    pkg = GenericPackager(item_type="DataPipeline")
    out_a = pkg.pack(
        source=src_dir,
        display_name="MyPipeline",
        target_folder="/",
        logical_id=None,
        staging_dir=tmp_path / "staging-a",
    )
    out_b = pkg.pack(
        source=src_dir,
        display_name="MyPipeline",
        target_folder="/",
        logical_id=None,
        staging_dir=tmp_path / "staging-b",
    )

    payload_a = json.loads((out_a / ".platform").read_text(encoding="utf-8"))
    payload_b = json.loads((out_b / ".platform").read_text(encoding="utf-8"))
    assert payload_a["config"]["logicalId"] == payload_b["config"]["logicalId"], (
        "logical_id must be byte-stable across re-runs (D-12)"
    )

    sidecar = tmp_path / ".sigantry" / "datapipeline-ids.json"
    assert sidecar.is_file(), f"sidecar {sidecar} should exist after pack"
    sidecar_payload = json.loads(sidecar.read_text(encoding="utf-8"))
    assert sidecar_payload["schema_version"] == "1.0"
    # Source is a directory; key is the directory's basename.
    assert "source" in sidecar_payload["ids"]
    assert sidecar_payload["ids"]["source"] == payload_a["config"]["logicalId"]


def test_generic_packager_uses_protocol_pack_signature(tmp_path) -> None:
    """LOAD-BEARING (D-03): pack(source, *, display_name, target_folder, logical_id, staging_dir) signature."""
    sig = inspect.signature(GenericPackager.pack)
    params = list(sig.parameters.values())

    assert params[0].name == "self"
    assert params[1].name == "source"
    assert params[1].kind == inspect.Parameter.POSITIONAL_OR_KEYWORD
    # Keyword-only block follows.
    keyword_only = [p for p in params[2:] if p.kind == inspect.Parameter.KEYWORD_ONLY]
    keyword_only_names = [p.name for p in keyword_only]
    assert keyword_only_names == [
        "display_name",
        "target_folder",
        "logical_id",
        "staging_dir",
    ]
    # Return annotation -- the module uses ``from __future__ import
    # annotations`` so the runtime form is a string. Compare against
    # the resolved type via ``get_type_hints`` (which evaluates the
    # forward references) AND the verbatim string for defence in depth.
    from typing import get_type_hints

    hints = get_type_hints(GenericPackager.pack)
    assert hints["return"] is Path, (
        f"return annotation resolved to {hints['return']!r}; expected Path"
    )
    assert sig.return_annotation == "Path", (
        f"return annotation should be the string 'Path' under "
        f"PEP-563 deferred evaluation; got {sig.return_annotation!r}"
    )


def test_packager_registry_isinstance_runtime_checkable(tmp_path) -> None:
    """LOAD-BEARING (D-03): PACKAGER_REGISTRY values pass isinstance(p, Packager) at runtime."""
    expected_keys = {
        "Notebook",
        "DataPipeline",
        "SemanticModel",
        "Report",
        "SparkJobDefinition",
    }
    assert set(PACKAGER_REGISTRY) == expected_keys, (
        f"PACKAGER_REGISTRY keys drift: expected {sorted(expected_keys)}, "
        f"got {sorted(PACKAGER_REGISTRY)}"
    )
    for key, packager in PACKAGER_REGISTRY.items():
        assert isinstance(packager, Packager), (
            f"{key} packager fails Protocol isinstance check (D-03)"
        )

    # Notebook routes through the dedicated NotebookPackager class.
    assert isinstance(PACKAGER_REGISTRY["Notebook"], NotebookPackager)
    # The other four are GenericPackager instances keyed by item_type.
    for key in ("DataPipeline", "SemanticModel", "Report", "SparkJobDefinition"):
        pkg = PACKAGER_REGISTRY[key]
        assert isinstance(pkg, GenericPackager)
        assert pkg.item_type == key
