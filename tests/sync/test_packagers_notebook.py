"""Plan 13-03 Task 1 -- NotebookPackager (SYNC-02 / D-11 / Council B).

Replaces the Wave 0 xfail stubs from Plan 13-00 with real assertions.
The 5 load-bearing test names below MUST stay byte-stable -- the
T-13-W0-LOAD-BEARING-NAMES invariant pins them as the contract surface
that Plan 13-00 reserved for this plan.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from sigantry_core.sync.packagers.notebook import NotebookPackager

_UUID4_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$")

_MINIMAL_IPYNB: dict = {
    "cells": [],
    "metadata": {},
    "nbformat": 4,
    "nbformat_minor": 5,
}


def _write_minimal_ipynb(path: Path) -> None:
    """Write a minimal-but-valid ``.ipynb`` payload at ``path``."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(_MINIMAL_IPYNB, indent=2) + "\n",
        encoding="utf-8",
    )


# --------------------------------------------------------------------------
# Load-bearing test names (T-13-W0-LOAD-BEARING-NAMES) -- DO NOT RENAME
# --------------------------------------------------------------------------


def test_notebook_packager_synthesises_platform_with_uuid4_logical_id(tmp_path) -> None:
    """First packaging mints a UUID4 logical_id and writes .platform schema 2.0."""
    src_dir = tmp_path / "notebooks"
    src = src_dir / "test.ipynb"
    _write_minimal_ipynb(src)
    staging = tmp_path / "staging"

    out = NotebookPackager().pack(
        source=src,
        display_name="MyNotebook",
        target_folder="/raw",
        logical_id=None,
        staging_dir=staging,
    )

    assert out == (staging / "raw" / "MyNotebook.Notebook").resolve()
    assert (out / "notebook-content.ipynb").is_file()
    assert (out / ".platform").is_file()

    payload = json.loads((out / ".platform").read_text(encoding="utf-8"))
    assert payload["metadata"]["type"] == "Notebook"
    assert payload["metadata"]["displayName"] == "MyNotebook"
    assert payload["config"]["version"] == "2.0"
    logical_id = payload["config"]["logicalId"]
    assert _UUID4_RE.match(logical_id), f"logicalId {logical_id!r} is not a UUID4"


def test_notebook_packager_persists_logical_id(tmp_path) -> None:
    """LOAD-BEARING (SYNC-02 / D-11): second packaging reads <source-dir>/.sigantry/notebook-ids.json and reuses the same logical_id."""
    src_dir = tmp_path / "notebooks"
    src = src_dir / "foo.ipynb"
    _write_minimal_ipynb(src)
    staging_a = tmp_path / "staging-a"
    staging_b = tmp_path / "staging-b"

    pkg = NotebookPackager()
    out_a = pkg.pack(
        source=src,
        display_name="Foo",
        target_folder="/",
        logical_id=None,
        staging_dir=staging_a,
    )
    out_b = pkg.pack(
        source=src,
        display_name="Foo",
        target_folder="/",
        logical_id=None,
        staging_dir=staging_b,
    )

    payload_a = json.loads((out_a / ".platform").read_text(encoding="utf-8"))
    payload_b = json.loads((out_b / ".platform").read_text(encoding="utf-8"))
    assert payload_a["config"]["logicalId"] == payload_b["config"]["logicalId"], (
        "logical_id must be byte-stable across re-runs (D-11)"
    )

    sidecar = src_dir / ".sigantry" / "notebook-ids.json"
    assert sidecar.is_file(), f"sidecar {sidecar} should exist after pack"
    sidecar_payload = json.loads(sidecar.read_text(encoding="utf-8"))
    assert sidecar_payload["schema_version"] == "1.0"
    assert "foo.ipynb" in sidecar_payload["ids"]
    assert _UUID4_RE.match(sidecar_payload["ids"]["foo.ipynb"])
    # Sidecar matches what landed in the .platform output.
    assert sidecar_payload["ids"]["foo.ipynb"] == payload_a["config"]["logicalId"]


def test_notebook_packager_lf_normalisation_extends_to_ipynb(tmp_path) -> None:
    """`.ipynb` is JSON; LF normalisation harmless but must be applied (D-11 + Phase 4 Pitfall 3)."""
    src_dir = tmp_path / "notebooks"
    src = src_dir / "crlf.ipynb"
    src_dir.mkdir(parents=True, exist_ok=True)
    # Synthesise CRLF + lone-CR mix.
    crlf_payload = (
        b'{\r\n  "cells": [],\r\n  "metadata": {},\r\n  '
        b'"nbformat": 4,\r\n  "nbformat_minor": 5\r}\r\n'
    )
    src.write_bytes(crlf_payload)
    staging = tmp_path / "staging"

    out = NotebookPackager().pack(
        source=src,
        display_name="Crlf",
        target_folder="/",
        logical_id=None,
        staging_dir=staging,
    )

    ipynb_bytes = (out / "notebook-content.ipynb").read_bytes()
    assert b"\r\n" not in ipynb_bytes, "LF normalisation should have stripped CRLF from .ipynb"
    assert b"\r" not in ipynb_bytes, "LF normalisation should have stripped lone CR from .ipynb"

    platform_bytes = (out / ".platform").read_bytes()
    assert b"\r\n" not in platform_bytes
    assert b"\r" not in platform_bytes


def test_notebook_packager_emits_platform_schema_v2_fields(tmp_path) -> None:
    """metadata.{type, displayName} + config.{version='2.0', logicalId} present (Council B)."""
    src_dir = tmp_path / "notebooks"
    src = src_dir / "v2.ipynb"
    _write_minimal_ipynb(src)
    staging = tmp_path / "staging"

    out = NotebookPackager().pack(
        source=src,
        display_name="V2",
        target_folder="/",
        logical_id=None,
        staging_dir=staging,
    )

    payload = json.loads((out / ".platform").read_text(encoding="utf-8"))
    assert set(payload.keys()) == {"$schema", "metadata", "config"}
    assert payload["$schema"].endswith("/2.0.0/schema.json")
    assert set(payload["metadata"].keys()) == {"type", "displayName"}
    assert set(payload["config"].keys()) == {"version", "logicalId"}


def test_notebook_packager_round_trip_9_fixture_ipynb(tmp_path) -> None:
    """SPEC acceptance: 9 raw .ipynb fixtures package; second run reads the same logical_ids."""
    src_dir = tmp_path / "notebooks"
    sources: list[Path] = []
    for idx in range(9):
        src = src_dir / f"{idx:02d}.ipynb"
        _write_minimal_ipynb(src)
        sources.append(src)

    pkg = NotebookPackager()

    # Pass 1.
    pass1_ids: list[str] = []
    for idx, src in enumerate(sources):
        out = pkg.pack(
            source=src,
            display_name=f"Item{idx:02d}",
            target_folder="/",
            logical_id=None,
            staging_dir=tmp_path / f"staging-1-{idx:02d}",
        )
        payload = json.loads((out / ".platform").read_text(encoding="utf-8"))
        pass1_ids.append(payload["config"]["logicalId"])

    # Pass 2 (different staging dirs, same sources).
    pass2_ids: list[str] = []
    for idx, src in enumerate(sources):
        out = pkg.pack(
            source=src,
            display_name=f"Item{idx:02d}",
            target_folder="/",
            logical_id=None,
            staging_dir=tmp_path / f"staging-2-{idx:02d}",
        )
        payload = json.loads((out / ".platform").read_text(encoding="utf-8"))
        pass2_ids.append(payload["config"]["logicalId"])

    assert pass1_ids == pass2_ids, "All 9 logical_ids must be byte-stable across packagings (D-11)"

    sidecar = src_dir / ".sigantry" / "notebook-ids.json"
    sidecar_payload = json.loads(sidecar.read_text(encoding="utf-8"))
    assert len(sidecar_payload["ids"]) == 9, (
        f"sidecar should have 9 entries; got {sidecar_payload['ids']}"
    )
    for idx in range(9):
        key = f"{idx:02d}.ipynb"
        assert key in sidecar_payload["ids"]
        assert sidecar_payload["ids"][key] == pass1_ids[idx]


# --------------------------------------------------------------------------
# Fabric-safe source normalisation (InvalidNotebookContent guard)
# --------------------------------------------------------------------------


def _pack_and_load_content(tmp_path: Path, notebook: dict, name: str = "nb.ipynb") -> dict:
    """Pack ``notebook`` and return the parsed ``notebook-content.ipynb``."""
    src_dir = tmp_path / "notebooks"
    src = src_dir / name
    src_dir.mkdir(parents=True, exist_ok=True)
    src.write_text(json.dumps(notebook, indent=1) + "\n", encoding="utf-8")
    out = NotebookPackager().pack(
        source=src,
        display_name="Nb",
        target_folder="/",
        logical_id=None,
        staging_dir=tmp_path / "staging",
    )
    return json.loads((out / "notebook-content.ipynb").read_text(encoding="utf-8"))


def test_notebook_packager_coerces_string_source_to_list(tmp_path) -> None:
    """Fabric guard: a cell whose ``source`` is a single string is rewritten
    to the list-of-lines form (``InvalidNotebookContent`` otherwise).

    The split is loss-less: ``"a\\nb\\n"`` -> ``["a\\n", "b\\n"]`` so
    joining the list reproduces the original text exactly.
    """
    nb = {
        "cells": [
            {"cell_type": "markdown", "metadata": {}, "source": "# Title\n\nbody line\n"},
            {
                "cell_type": "code",
                "metadata": {},
                "execution_count": None,
                "outputs": [],
                "source": "x = 1\nprint(x)",
            },
        ],
        "metadata": {},
        "nbformat": 4,
        "nbformat_minor": 5,
    }
    content = _pack_and_load_content(tmp_path, nb)
    for cell in content["cells"]:
        assert isinstance(cell["source"], list), cell
        assert all(isinstance(line, str) for line in cell["source"])
    # Loss-less round trip.
    assert "".join(content["cells"][0]["source"]) == "# Title\n\nbody line\n"
    assert "".join(content["cells"][1]["source"]) == "x = 1\nprint(x)"


def test_notebook_packager_coerces_output_text_to_list(tmp_path) -> None:
    """Output ``text`` / ``traceback`` strings are coerced too (same Fabric cast)."""
    nb = {
        "cells": [
            {
                "cell_type": "code",
                "metadata": {},
                "execution_count": 1,
                "source": ["print('hi')\n"],
                "outputs": [
                    {"output_type": "stream", "name": "stdout", "text": "hi\nthere\n"},
                ],
            },
        ],
        "metadata": {},
        "nbformat": 4,
        "nbformat_minor": 5,
    }
    content = _pack_and_load_content(tmp_path, nb)
    out = content["cells"][0]["outputs"][0]
    assert isinstance(out["text"], list)
    assert "".join(out["text"]) == "hi\nthere\n"


def test_notebook_packager_leaves_list_source_byte_identical(tmp_path) -> None:
    """Already-compliant notebooks take the byte-identical fast path.

    A notebook whose every ``source`` is already a list must not be
    re-serialised -- the staged ``notebook-content.ipynb`` bytes equal the
    LF-normalised source bytes (SemVer-safe; no churn for valid input).
    """
    nb = {
        "cells": [
            {
                "cell_type": "code",
                "metadata": {},
                "execution_count": None,
                "outputs": [],
                "source": ["a = 1\n", "b = 2\n"],
            },
        ],
        "metadata": {},
        "nbformat": 4,
        "nbformat_minor": 5,
    }
    src_dir = tmp_path / "notebooks"
    src = src_dir / "nb.ipynb"
    src_dir.mkdir(parents=True, exist_ok=True)
    src.write_text(json.dumps(nb, indent=1) + "\n", encoding="utf-8")
    expected = src.read_bytes().replace(b"\r\n", b"\n").replace(b"\r", b"\n")

    out = NotebookPackager().pack(
        source=src,
        display_name="Nb",
        target_folder="/",
        logical_id=None,
        staging_dir=tmp_path / "staging",
    )
    assert (out / "notebook-content.ipynb").read_bytes() == expected
