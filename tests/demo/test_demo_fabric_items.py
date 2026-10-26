"""Plan 15-01 / DEMO-02 -- the 3 sample fabric_items trees.

Ships templates/demo/fabric_items/{LoadOrders.Notebook,
RefreshOrdersDaily.DataPipeline, OrdersAnalytics.SemanticModel}/ trees
with .platform schema 2.0 + placeholder logicalIds
00000000-0000-0000-0000-00000000d002..d004 per CONTEXT D-03 + Issue #70
(Sales.Lakehouse removed because sync apply has no Lakehouse packager,
and LoadOrders.Notebook converted to notebook-content.ipynb).

Local-import idiom matches tests/starter/test_starter_content.py:20.
"""

from __future__ import annotations

import json
from pathlib import Path

_FABRIC_ITEMS_DIR = Path(__file__).resolve().parents[2] / "templates" / "demo" / "fabric_items"

_ITEMS = (
    "LoadOrders.Notebook",
    "RefreshOrdersDaily.DataPipeline",
    "OrdersAnalytics.SemanticModel",
)

_EXPECTED_LOGICAL_IDS = {
    "LoadOrders.Notebook": "00000000-0000-0000-0000-00000000d002",
    "RefreshOrdersDaily.DataPipeline": "00000000-0000-0000-0000-00000000d003",
    "OrdersAnalytics.SemanticModel": "00000000-0000-0000-0000-00000000d004",
}

_EXPECTED_TYPES = {
    "LoadOrders.Notebook": "Notebook",
    "RefreshOrdersDaily.DataPipeline": "DataPipeline",
    "OrdersAnalytics.SemanticModel": "SemanticModel",
}


def test_demo_fabric_items_have_platform_files() -> None:
    """Each of the 3 sample item directories carries a Microsoft Fabric `.platform` file."""
    actual_dirs = sorted(p.name for p in _FABRIC_ITEMS_DIR.iterdir() if p.is_dir())
    assert actual_dirs == sorted(_ITEMS), actual_dirs
    for item in _ITEMS:
        platform = _FABRIC_ITEMS_DIR / item / ".platform"
        assert platform.is_file(), f"missing .platform for {item}: {platform}"


def test_demo_fabric_items_carry_placeholder_logical_ids() -> None:
    """logicalIds are 00000000-0000-0000-0000-00000000d002..d004 (placeholder, never real workspace IDs)."""
    for item, expected_lid in _EXPECTED_LOGICAL_IDS.items():
        platform = _FABRIC_ITEMS_DIR / item / ".platform"
        data = json.loads(platform.read_text(encoding="utf-8"))
        actual_lid = data["config"]["logicalId"]
        assert actual_lid == expected_lid, (item, actual_lid)
        actual_type = data["metadata"]["type"]
        assert actual_type == _EXPECTED_TYPES[item], (item, actual_type)


def test_sales_lakehouse_is_not_shipped_in_demo_fabric_items() -> None:
    """Sales.Lakehouse is omitted from templates/demo/fabric_items/ (Issue #70)."""
    assert not (_FABRIC_ITEMS_DIR / "Sales.Lakehouse").exists()


def test_notebook_content_ipynb_is_valid_synapse_pyspark_notebook() -> None:
    """LoadOrders.Notebook/notebook-content.ipynb is a valid .ipynb with list-of-lines cell source."""
    notebook_dir = _FABRIC_ITEMS_DIR / "LoadOrders.Notebook"
    assert not (notebook_dir / "notebook-content.py").exists()
    notebook_path = notebook_dir / "notebook-content.ipynb"
    assert notebook_path.is_file(), notebook_path
    data = json.loads(notebook_path.read_text(encoding="utf-8"))
    assert data["nbformat"] == 4
    assert data["metadata"]["kernel_info"]["name"] == "synapse_pyspark"
    cells = data["cells"]
    assert isinstance(cells, list) and len(cells) >= 1
    source = cells[0]["source"]
    assert isinstance(source, list)
    assert any("Demo: load_orders" in line for line in source)


def test_data_pipeline_content_minimal_valid() -> None:
    """RefreshOrdersDaily.DataPipeline/pipeline-content.json parses + carries the minimal activity set."""
    pipeline_path = _FABRIC_ITEMS_DIR / "RefreshOrdersDaily.DataPipeline" / "pipeline-content.json"
    data = json.loads(pipeline_path.read_text(encoding="utf-8"))
    assert data == {"properties": {"activities": []}}, data


def test_semantic_model_definition_pbism_valid_json() -> None:
    """OrdersAnalytics.SemanticModel/definition.pbism is valid JSON."""
    pbism_path = _FABRIC_ITEMS_DIR / "OrdersAnalytics.SemanticModel" / "definition.pbism"
    data = json.loads(pbism_path.read_text(encoding="utf-8"))
    assert data == {"version": "4.0", "settings": {}}, data


def test_semantic_model_tmdl_files_present() -> None:
    """OrdersAnalytics.SemanticModel/definition/ contains the .tmdl table + measure files."""
    sm_root = _FABRIC_ITEMS_DIR / "OrdersAnalytics.SemanticModel" / "definition"

    model_text = (sm_root / "model.tmdl").read_text(encoding="utf-8")
    assert "model Model" in model_text
    assert "culture: en-US" in model_text

    db_text = (sm_root / "database.tmdl").read_text(encoding="utf-8")
    assert "compatibilityLevel: 1567" in db_text

    orders_text = (sm_root / "tables" / "Orders.tmdl").read_text(encoding="utf-8")
    assert "table Orders" in orders_text
    assert "column 'OrderId'" in orders_text
    assert "column 'Amount'" in orders_text
    assert "measure 'Total Sales'" in orders_text
    assert "SUM('Orders'[Amount])" in orders_text
