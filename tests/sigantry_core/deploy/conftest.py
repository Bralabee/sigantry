"""Shared fixtures for sigantry_core.deploy tests (Plan 04-01)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from sigantry_core.auth import TokenProvider


@pytest.fixture
def mock_token_provider() -> MagicMock:
    """MagicMock with TokenProvider spec — spans .get_credential / .get_token / .tenant_id.

    Used by every deploy_workspace test to avoid spinning up a real
    DefaultAzureCredential chain.
    """
    tp = MagicMock(spec=TokenProvider)
    tp.get_credential.return_value = MagicMock(name="FakeTokenCredential")
    tp.get_token.return_value = "test-token-xyz"
    tp.last_credential_class.return_value = "MockCredential"
    tp.tenant_id = "11111111-2222-3333-4444-555555555555"
    return tp


@pytest.fixture
def tmp_item_tree(tmp_path: Path) -> Path:
    """Minimal on-disk item tree: one Lakehouse + one Notebook with .platform files."""
    tree = tmp_path / "fabric_items"
    (tree / "Bronze.Lakehouse").mkdir(parents=True)
    (tree / "Bronze.Lakehouse" / ".platform").write_text(
        '{"version":"2.0","config":{"logicalId":"11111111-1111-1111-1111-111111111111"},'
        '"metadata":{"type":"Lakehouse","displayName":"Bronze"}}\n',
        encoding="utf-8",
    )
    (tree / "Ingest.Notebook").mkdir(parents=True)
    (tree / "Ingest.Notebook" / ".platform").write_text(
        '{"version":"2.0","config":{"logicalId":"22222222-2222-2222-2222-222222222222"},'
        '"metadata":{"type":"Notebook","displayName":"Ingest"}}\n',
        encoding="utf-8",
    )
    (tree / "parameters.yml").write_text(
        "find_replace: []\nkey_value_replace: []\n",
        encoding="utf-8",
    )
    return tree
