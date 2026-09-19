"""Unit tests for scripts/pre_commit/check_logical_id.py (Pitfall 3 mitigation)."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest


def _load_module():
    repo_root = Path(__file__).resolve().parents[2]
    script = repo_root / "scripts" / "pre_commit" / "check_logical_id.py"
    spec = importlib.util.spec_from_file_location("check_logical_id_under_test", script)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _write_platform(path: Path, logical_id: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "config": {"logicalId": logical_id},
                "metadata": {
                    "type": "Lakehouse",
                    "displayName": path.parent.name,
                },
            }
        ),
        encoding="utf-8",
    )


def test_exits_zero_on_unique_ids(tmp_path: Path) -> None:
    mod = _load_module()
    _write_platform(
        tmp_path / "A.Lakehouse" / ".platform",
        "11111111-1111-1111-1111-111111111111",
    )
    _write_platform(
        tmp_path / "B.Lakehouse" / ".platform",
        "22222222-2222-2222-2222-222222222222",
    )
    assert mod.main(str(tmp_path)) == 0


def test_exits_one_on_duplicate(tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
    mod = _load_module()
    shared = "11111111-1111-1111-1111-111111111111"
    _write_platform(tmp_path / "A.Lakehouse" / ".platform", shared)
    _write_platform(tmp_path / "B.Lakehouse" / ".platform", shared)
    rc = mod.main(str(tmp_path))
    captured = capsys.readouterr()
    assert rc == 1
    assert "DUPLICATE logicalId" in captured.err
    assert shared in captured.err
    # Both offending paths printed
    assert "A.Lakehouse" in captured.err
    assert "B.Lakehouse" in captured.err


def test_skips_virtualenv_dirs(tmp_path: Path) -> None:
    mod = _load_module()
    # Duplicate that lives inside .venv MUST NOT be counted.
    _write_platform(
        tmp_path / "A.Lakehouse" / ".platform",
        "11111111-1111-1111-1111-111111111111",
    )
    _write_platform(
        tmp_path / ".venv" / "A.Lakehouse" / ".platform",
        "11111111-1111-1111-1111-111111111111",
    )
    assert mod.main(str(tmp_path)) == 0


def test_malformed_platform_returns_one(tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
    mod = _load_module()
    (tmp_path / "Bad.Lakehouse").mkdir()
    (tmp_path / "Bad.Lakehouse" / ".platform").write_text("{not json", encoding="utf-8")
    assert mod.main(str(tmp_path)) == 1
    assert "ERROR" in capsys.readouterr().err


def test_accepts_platform_file_arg(tmp_path: Path) -> None:
    """Pre-commit may pass a specific .platform path; hook walks from its grandparent."""
    mod = _load_module()
    _write_platform(
        tmp_path / "A.Lakehouse" / ".platform",
        "11111111-1111-1111-1111-111111111111",
    )
    _write_platform(
        tmp_path / "B.Lakehouse" / ".platform",
        "22222222-2222-2222-2222-222222222222",
    )
    # Passing the .platform file resolves to walking tmp_path; unique -> 0.
    platform_file = tmp_path / "A.Lakehouse" / ".platform"
    assert mod.main(str(platform_file)) == 0
