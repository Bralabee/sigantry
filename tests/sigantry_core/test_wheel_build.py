"""Integration test: build the wheel and assert its filename + metadata.

This test is slower than the rest of the sigantry_core suite (~8s) because it
shells out to `python -m build`. It is marked `slow` so the developer fast
loop skips it. CI runs with no marker filter, so it runs there.

Mitigates Pitfall P1-4 (version drift) and threat T-1-03 (wheel supply chain):
a drifted version or broken metadata fails this test before twine upload.
"""

from __future__ import annotations

import pathlib
import subprocess
import sys
import tempfile
import zipfile

import pytest

WHEEL_NAME = "sigantry-1.0.0-py3-none-any.whl"


@pytest.mark.slow
def test_wheel_builds_with_expected_filename(repo_root: pathlib.Path) -> None:
    with tempfile.TemporaryDirectory() as out:
        subprocess.run(
            [sys.executable, "-m", "build", "--wheel", "--outdir", out],
            cwd=repo_root,
            check=True,
            capture_output=True,
            text=True,
        )
        produced = list(pathlib.Path(out).glob("*.whl"))
        assert len(produced) == 1, f"expected exactly one wheel, got {produced}"
        assert produced[0].name == WHEEL_NAME, (
            f"wheel filename drifted: {produced[0].name!r} != {WHEEL_NAME!r}"
        )


@pytest.mark.slow
def test_wheel_passes_twine_check(repo_root: pathlib.Path) -> None:
    with tempfile.TemporaryDirectory() as out:
        subprocess.run(
            [sys.executable, "-m", "build", "--wheel", "--outdir", out],
            cwd=repo_root,
            check=True,
            capture_output=True,
        )
        wheel_path = next(pathlib.Path(out).glob("*.whl"))
        result = subprocess.run(
            [sys.executable, "-m", "twine", "check", str(wheel_path)],
            shell=False,
            cwd=repo_root,
            capture_output=True,
            text=True,
        )
        # twine check returns 0 on success; stdout contains PASSED per file.
        assert result.returncode == 0, result.stdout + result.stderr
        assert "PASSED" in result.stdout, result.stdout


@pytest.mark.slow
def test_wheel_contents_match_package_tree(repo_root: pathlib.Path) -> None:
    with tempfile.TemporaryDirectory() as out:
        subprocess.run(
            [sys.executable, "-m", "build", "--wheel", "--outdir", out],
            cwd=repo_root,
            check=True,
            capture_output=True,
        )
        wheel = next(pathlib.Path(out).glob("*.whl"))
        with zipfile.ZipFile(wheel) as zf:
            names = set(zf.namelist())
        required = {
            "sigantry_core/__init__.py",
            "sigantry_core/_version.py",
            "sigantry_core/py.typed",
            "sigantry_core/cli.py",
            "sigantry_core/auth/__init__.py",
            "sigantry_core/client/__init__.py",
            "sigantry_core/workspace/__init__.py",
            "sigantry_core/capacity/__init__.py",
            "sigantry_core/governance/__init__.py",
            "sigantry_core/monitor/__init__.py",
            "sigantry_core/deploy/__init__.py",
            "sigantry_core/pipelines/__init__.py",
            "sigantry_core/preflight/__init__.py",
            "sigantry_core/purview/__init__.py",
            "sigantry_core/utils/__init__.py",
        }
        missing = required - names
        assert not missing, f"wheel missing files: {sorted(missing)}"
