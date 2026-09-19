"""Plan 15-01 / DEMO-02 -- the 4 sample fabric_items trees.

Plan 15-01 ships templates/demo/fabric_items/{Sales.Lakehouse,
LoadOrders.Notebook, RefreshOrdersDaily.DataPipeline,
OrdersAnalytics.SemanticModel}/ trees with .platform schema 2.0 +
placeholder logicalIds 00000000-0000-0000-0000-00000000d001..d004
per CONTEXT D-03 + RESEARCH §Pattern 2.

Local-import idiom matches tests/starter/test_starter_content.py:20.
"""

from __future__ import annotations

import json
from pathlib import Path

_FABRIC_ITEMS_DIR = Path(__file__).resolve().parents[2] / "templates" / "demo" / "fabric_items"

_ITEMS = (
    "Sales.Lakehouse",
    "LoadOrders.Notebook",
    "RefreshOrdersDaily.DataPipeline",
    "OrdersAnalytics.SemanticModel",
)

_EXPECTED_LOGICAL_IDS = {
    "Sales.Lakehouse": "00000000-0000-0000-0000-00000000d001",
    "LoadOrders.Notebook": "00000000-0000-0000-0000-00000000d002",
    "RefreshOrdersDaily.DataPipeline": "00000000-0000-0000-0000-00000000d003",
    "OrdersAnalytics.SemanticModel": "00000000-0000-0000-0000-00000000d004",
}

_EXPECTED_TYPES = {
    "Sales.Lakehouse": "Lakehouse",
    "LoadOrders.Notebook": "Notebook",
    "RefreshOrdersDaily.DataPipeline": "DataPipeline",
    "OrdersAnalytics.SemanticModel": "SemanticModel",
}


def test_demo_fabric_items_have_platform_files() -> None:
    """Each of the 4 sample item directories carries a Microsoft Fabric `.platform` file."""
    for item in _ITEMS:
        platform = _FABRIC_ITEMS_DIR / item / ".platform"
        assert platform.is_file(), f"missing .platform for {item}: {platform}"


def test_demo_fabric_items_carry_placeholder_logical_ids() -> None:
    """logicalIds are 00000000-0000-0000-0000-00000000d001..d004 (placeholder, never real workspace IDs)."""
    for item, expected_lid in _EXPECTED_LOGICAL_IDS.items():
        platform = _FABRIC_ITEMS_DIR / item / ".platform"
        data = json.loads(platform.read_text(encoding="utf-8"))
        actual_lid = data["config"]["logicalId"]
        assert actual_lid == expected_lid, (item, actual_lid)
        actual_type = data["metadata"]["type"]
        assert actual_type == _EXPECTED_TYPES[item], (item, actual_type)


def test_lakehouse_metadata_json_default_schema_dbo() -> None:
    """Sales.Lakehouse/lakehouse.metadata.json declares defaultSchema=dbo."""
    metadata_path = _FABRIC_ITEMS_DIR / "Sales.Lakehouse" / "lakehouse.metadata.json"
    data = json.loads(metadata_path.read_text(encoding="utf-8"))
    assert data == {"defaultSchema": "dbo"}, data

    shortcuts_path = _FABRIC_ITEMS_DIR / "Sales.Lakehouse" / "shortcuts.metadata.json"
    shortcuts = json.loads(shortcuts_path.read_text(encoding="utf-8"))
    assert shortcuts == {"shortcuts": []}, shortcuts


def test_notebook_content_py_carries_metadata_block() -> None:
    """LoadOrders.Notebook/notebook-content.py contains the `# META` metadata block."""
    notebook_path = _FABRIC_ITEMS_DIR / "LoadOrders.Notebook" / "notebook-content.py"
    text = notebook_path.read_text(encoding="utf-8")
    assert "# METADATA" in text
    assert "synapse_pyspark" in text
    assert "# CELL" in text
    assert "Demo: load_orders" in text


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
