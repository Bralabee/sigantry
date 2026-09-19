"""Unit tests for scripts/pre_commit/check_crlf.py (Pitfall 3 mitigation)."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest


def _load_module():
    repo_root = Path(__file__).resolve().parents[2]
    script = repo_root / "scripts" / "pre_commit" / "check_crlf.py"
    spec = importlib.util.spec_from_file_location("check_crlf_under_test", script)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _make_item(root: Path, *, with_crlf_in: tuple[str, ...] = ()) -> None:
    item = root / "Bronze.Lakehouse"
    item.mkdir(parents=True)

    platform_bytes = (
        b'{"config":{"logicalId":"x"},"metadata":{"type":"Lakehouse","displayName":"B"}}\n'
    )
    if ".platform" in with_crlf_in:
        platform_bytes = platform_bytes.replace(b"\n", b"\r\n")
    (item / ".platform").write_bytes(platform_bytes)

    py_bytes = b'print("hi")\n'
    if "py" in with_crlf_in:
        py_bytes = py_bytes.replace(b"\n", b"\r\n")
    (item / "notebook-content.py").write_bytes(py_bytes)

    json_bytes = b'{"a": 1}\n'
    if "json" in with_crlf_in:
        json_bytes = json_bytes.replace(b"\n", b"\r\n")
    (item / "artifact-config.json").write_bytes(json_bytes)


def test_exits_zero_when_all_lf(tmp_path: Path) -> None:
    mod = _load_module()
    _make_item(tmp_path)
    assert mod.main(str(tmp_path)) == 0


def test_rejects_crlf_in_platform(tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
    mod = _load_module()
    _make_item(tmp_path, with_crlf_in=(".platform",))
    rc = mod.main(str(tmp_path))
    captured = capsys.readouterr()
    assert rc == 1
    assert "CRLF" in captured.err
    assert ".platform" in captured.err


def test_rejects_crlf_in_sibling_py(tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
    mod = _load_module()
    _make_item(tmp_path, with_crlf_in=("py",))
    rc = mod.main(str(tmp_path))
    assert rc == 1
    assert "notebook-content.py" in capsys.readouterr().err


def test_rejects_crlf_in_sibling_json(tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
    mod = _load_module()
    _make_item(tmp_path, with_crlf_in=("json",))
    rc = mod.main(str(tmp_path))
    assert rc == 1
    assert "artifact-config.json" in capsys.readouterr().err


def test_ignores_py_outside_item_folders(tmp_path: Path) -> None:
    """A .py outside any .platform-containing folder should NOT be scanned."""
    mod = _load_module()
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts" / "tool.py").write_bytes(b'print("hi")\r\n')  # CRLF
    # No .platform anywhere -> zero targets scanned -> exit 0.
    assert mod.main(str(tmp_path)) == 0


def test_skips_virtualenv_dirs(tmp_path: Path) -> None:
    mod = _load_module()
    # CRLF in .venv must NOT be reported.
    venv_item = tmp_path / ".venv" / "Bronze.Lakehouse"
    venv_item.mkdir(parents=True)
    (venv_item / ".platform").write_bytes(b'{"config":{"logicalId":"x"},"metadata":{}}\r\n')
    assert mod.main(str(tmp_path)) == 0
