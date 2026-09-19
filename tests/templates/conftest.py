"""Shared fixtures for ADO YAML template structural tests (Plan 05-01)."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

TEMPLATE_ROOT = Path("templates")
TEMPLATE_FILES: list[Path] = sorted(TEMPLATE_ROOT.rglob("*.yml"))


@pytest.fixture(scope="session")
def template_files() -> list[Path]:
    """List of every shipped template YAML file (relative to repo root)."""
    return TEMPLATE_FILES


@pytest.fixture(scope="session")
def parsed_templates() -> dict[str, object]:
    """Map of template path -> parsed YAML document."""
    parsed: dict[str, object] = {}
    for p in TEMPLATE_FILES:
        parsed[str(p)] = yaml.safe_load(p.read_text(encoding="utf-8"))
    return parsed


@pytest.fixture(scope="session")
def extends_template() -> dict:
    """templates/extends/secure-pipeline.yml parsed (Plan 08-03 rename)."""
    doc = yaml.safe_load(Path("templates/extends/secure-pipeline.yml").read_text(encoding="utf-8"))
    assert isinstance(doc, dict)
    return doc


@pytest.fixture(scope="session")
def extends_source() -> str:
    """templates/extends/secure-pipeline.yml raw text (for string matches)."""
    return Path("templates/extends/secure-pipeline.yml").read_text(encoding="utf-8")
