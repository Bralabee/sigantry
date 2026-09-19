"""Unit tests for sigantry_core.deploy.dependency (DEPLOY-04, T-4-09)."""

from __future__ import annotations

from pathlib import Path

import pytest

from sigantry_core.deploy.dependency import (
    _ITEM_REF_RE,
    DependencyCycleError,
    validate_order,
)


def _make_platform(dir_: Path, *, item_type: str, display_name: str) -> None:
    dir_.mkdir(parents=True, exist_ok=True)
    payload = (
        '{"version":"2.0","config":{"logicalId":"00000000-0000-0000-0000-000000000001"},'
        f'"metadata":{{"type":"{item_type}","displayName":"{display_name}"}}}}\n'
    )
    (dir_ / ".platform").write_text(payload, encoding="utf-8")


def test_dependency_cycle_error_is_runtime_error() -> None:
    assert issubclass(DependencyCycleError, RuntimeError)


def test_acyclic_tree_emits_dot_file(tmp_path: Path) -> None:
    """Happy path: Notebook 'Ingest' references Lakehouse 'Bronze'. DOT file
    written; digraph header present; edge recorded."""
    repo = tmp_path / "fabric_items"
    _make_platform(repo / "Bronze.Lakehouse", item_type="Lakehouse", display_name="Bronze")
    _make_platform(repo / "Ingest.Notebook", item_type="Notebook", display_name="Ingest")
    (repo / "Ingest.Notebook" / "notebook-content.py").write_text(
        '# META connections: {"lakehouse": "$items.Lakehouse.Bronze.$id"}\n',
        encoding="utf-8",
    )

    out_dir = tmp_path / "artefacts"
    path = validate_order(
        repository_directory=repo,
        item_type_in_scope=["Lakehouse", "Notebook"],
        dot_output_dir=out_dir,
    )
    dot = Path(path)
    assert dot.exists()
    text = dot.read_text(encoding="utf-8")
    assert text.startswith("digraph deploy {")
    assert '"Lakehouse/Bronze"' in text
    assert '"Notebook/Ingest"' in text
    assert '"Notebook/Ingest" -> "Lakehouse/Bronze";' in text


def test_empty_tree_returns_empty_digraph(tmp_path: Path) -> None:
    """Empty repo -> DOT with no nodes, no error."""
    repo = tmp_path / "empty_repo"
    repo.mkdir()
    out_dir = tmp_path / "artefacts"
    path = validate_order(
        repository_directory=repo,
        item_type_in_scope=[],
        dot_output_dir=out_dir,
    )
    dot = Path(path).read_text(encoding="utf-8")
    assert "digraph deploy {" in dot
    # No item nodes or edges.
    assert '"Lakehouse/' not in dot
    assert "->" not in dot


def test_cycle_detected_raises(tmp_path: Path) -> None:
    """A -> B -> A cycle via $items refs in Notebook body files.

    Notebook 'A' references Notebook 'B'; Notebook 'B' references Notebook
    'A'. Kahn's algorithm must detect the 2-cycle and raise.
    """
    repo = tmp_path / "cyclic"
    _make_platform(repo / "A.Notebook", item_type="Notebook", display_name="A")
    _make_platform(repo / "B.Notebook", item_type="Notebook", display_name="B")
    (repo / "A.Notebook" / "notebook-content.py").write_text(
        "# ref: $items.Notebook.B.$id\n", encoding="utf-8"
    )
    (repo / "B.Notebook" / "notebook-content.py").write_text(
        "# ref: $items.Notebook.A.$id\n", encoding="utf-8"
    )
    with pytest.raises(DependencyCycleError, match="Cycle detected"):
        validate_order(
            repository_directory=repo,
            item_type_in_scope=["Notebook"],
            dot_output_dir=tmp_path / "out",
        )


def test_dot_file_written_atomically(tmp_path: Path) -> None:
    """No partial-write .dot.tmp must remain after success."""
    repo = tmp_path / "fabric_items"
    _make_platform(repo / "L.Lakehouse", item_type="Lakehouse", display_name="L")
    out_dir = tmp_path / "artefacts"
    validate_order(
        repository_directory=repo,
        item_type_in_scope=["Lakehouse"],
        dot_output_dir=out_dir,
    )
    # Only dep-graph.dot should exist, NOT dep-graph.dot.tmp.
    produced = sorted(p.name for p in out_dir.iterdir())
    assert produced == ["dep-graph.dot"]


def test_non_canonical_types_recorded_but_not_scanned(tmp_path: Path) -> None:
    """Warehouse is non-canonical — it becomes a node but body references
    inside are ignored (no outgoing edges)."""
    repo = tmp_path / "fabric_items"
    _make_platform(repo / "W.Warehouse", item_type="Warehouse", display_name="W")
    # Body file with a $items ref. Because Warehouse is non-canonical, the
    # body scan is skipped — no edge is created.
    (repo / "W.Warehouse" / "ignored.json").write_text(
        '{"depends_on": "$items.Notebook.Downstream.$id"}\n', encoding="utf-8"
    )
    path = validate_order(
        repository_directory=repo,
        item_type_in_scope=["Warehouse"],
        dot_output_dir=tmp_path / "out",
    )
    dot = Path(path).read_text(encoding="utf-8")
    assert '"Warehouse/W"' in dot
    # No edge was created despite the body ref.
    assert "->" not in dot


def test_item_ref_regex_captures_three_forms() -> None:
    """Regex must capture: $items.Lakehouse.Bronze,
    $items.Notebook.Ingest.$id, $items.DataPipeline.DailyLoad.$id."""
    samples = [
        "$items.Lakehouse.Bronze",
        "$items.Notebook.Ingest.$id",
        "$items.DataPipeline.DailyLoad.$id",
    ]
    expected = [
        ("Lakehouse", "Bronze"),
        ("Notebook", "Ingest"),
        ("DataPipeline", "DailyLoad"),
    ]
    for sample, (exp_type, exp_name) in zip(samples, expected, strict=True):
        m = _ITEM_REF_RE.search(sample)
        assert m is not None, f"no match for {sample!r}"
        assert (m.group("type"), m.group("name")) == (exp_type, exp_name)


def test_self_reference_ignored(tmp_path: Path) -> None:
    """A body file that references its own item must not create a self-loop."""
    repo = tmp_path / "fabric_items"
    _make_platform(repo / "Solo.Notebook", item_type="Notebook", display_name="Solo")
    (repo / "Solo.Notebook" / "notebook-content.py").write_text(
        "# self-ref: $items.Notebook.Solo.$id\n", encoding="utf-8"
    )
    path = validate_order(
        repository_directory=repo,
        item_type_in_scope=["Notebook"],
        dot_output_dir=tmp_path / "out",
    )
    dot = Path(path).read_text(encoding="utf-8")
    # Node exists but no self-edge.
    assert '"Notebook/Solo"' in dot
    assert "->" not in dot
