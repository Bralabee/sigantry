"""Integration test: build the wheel and assert its filename + metadata.

This test is slower than the rest of the sigantry_core suite (~8s) because it
shells out to `python -m build`. It is marked `slow` so the developer fast
loop skips it. CI runs with no marker filter, so it runs there.

Mitigates Pitfall P1-4 (version drift) and threat T-1-03 (wheel supply chain):
a drifted version or broken metadata fails this test before twine upload.
"""

from __future__ import annotations

import pathlib
import re
import subprocess
import sys
import tarfile
import tempfile
import zipfile

import pytest
from packaging.version import Version

_VERSION_RE = re.compile(r'^__version__\s*=\s*"(\d+\.\d+\.\d+[^"]*)"$', re.MULTILINE)


def _version(repo_root: pathlib.Path) -> str:
    """The version ``_version.py`` declares, normalised as the build backend writes it.

    Derived, never restated: a literal here is a second copy of the version,
    which goes red on a correct release bump.
    """
    source = (repo_root / "sigantry_core" / "_version.py").read_text(encoding="utf-8")
    match = _VERSION_RE.search(source)
    assert match is not None, "_version.py must declare __version__ as a quoted SemVer string"
    return str(Version(match.group(1)))


def _wheel_name(repo_root: pathlib.Path) -> str:
    return f"sigantry-{_version(repo_root)}-py3-none-any.whl"


# What the wheel and the sdist may carry. Every member must match one pattern
# and every pattern must match a member, so a file that reaches a distribution
# without being listed here fails, and so does one that goes missing. The
# package patterns serve both; each distribution adds its own.
_PACKAGE_MEMBERS = (
    r"sigantry_core/(?:[a-z0-9_]+/)*[a-z0-9_]+\.py",
    r"sigantry_core/py\.typed",
    r"sigantry_core/sync/templates/[A-Za-z]+\.platform\.j2",
)
_WHEEL_METADATA = ("METADATA", "WHEEL", "RECORD", "entry_points.txt", "licenses/LICENSE")
_SDIST_FILES = (".gitignore", "CHANGELOG.md", "LICENSE", "PKG-INFO", "README.md", "pyproject.toml")


def _allowed(kind: str, version: str) -> list[str]:
    if kind == "wheel":
        info = re.escape(f"sigantry-{version}.dist-info/")
        return [*_PACKAGE_MEMBERS, *(info + re.escape(name) for name in _WHEEL_METADATA)]
    top = re.escape(f"sigantry-{version}/")
    return [top + p for p in (*_PACKAGE_MEMBERS, *map(re.escape, _SDIST_FILES))]


def _off_list(members: list[str], allowed: list[str]) -> tuple[list[str], list[str]]:
    """(members no pattern allows, patterns no member matches)."""
    compiled = [re.compile(p) for p in allowed]
    stray = sorted(m for m in members if not any(c.fullmatch(m) for c in compiled))
    unused = [
        p for p, c in zip(allowed, compiled, strict=True) if not any(map(c.fullmatch, members))
    ]
    return stray, unused


def test_the_member_allow_list_fails_on_a_planted_and_on_a_missing_member() -> None:
    """The allow-list can fail in both directions, checked without a build."""
    members = [
        "sigantry_core/__init__.py",
        "sigantry_core/sync/core.py",
        "sigantry_core/py.typed",
        "sigantry_core/sync/templates/Report.platform.j2",
        *(f"sigantry-1.2.3.dist-info/{name}" for name in _WHEEL_METADATA),
    ]
    allowed = _allowed("wheel", "1.2.3")
    assert _off_list(members, allowed) == ([], [])
    planted = [*members, "sigantry_core/capacity/TODO-phase-3.md", "tests/test_cli.py"]
    assert _off_list(planted, allowed)[0] == [
        "sigantry_core/capacity/TODO-phase-3.md",
        "tests/test_cli.py",
    ]
    missing = [m for m in members if m != "sigantry_core/py.typed"]
    assert _off_list(missing, allowed)[1] == [r"sigantry_core/py\.typed"]
    sdist = [f"sigantry-1.2.3/{m}" for m in (*members[:4], *_SDIST_FILES)]
    assert _off_list(sdist, _allowed("sdist", "1.2.3")) == ([], [])
    assert _off_list(
        [*sdist, "sigantry-1.2.3/.github/workflows/ci.yml"], _allowed("sdist", "1.2.3")
    )[0]


@pytest.mark.slow
def test_wheel_and_sdist_carry_only_allowed_members(repo_root: pathlib.Path) -> None:
    """Both distributions, built as CI builds them, hold exactly the listed members.

    `python -m build` with no flag builds the sdist and then the wheel from
    that sdist, which is what ci.yml's build job runs and what is published.
    """
    version = _version(repo_root)
    with tempfile.TemporaryDirectory() as out:
        subprocess.run(
            [sys.executable, "-m", "build", "--outdir", out],
            cwd=repo_root,
            check=True,
            capture_output=True,
        )
        wheel = pathlib.Path(out) / _wheel_name(repo_root)
        with zipfile.ZipFile(wheel) as zf:
            wheel_members = zf.namelist()
        with tarfile.open(pathlib.Path(out) / f"sigantry-{version}.tar.gz") as tf:
            sdist_members = [m.name for m in tf.getmembers()]
    for kind, members in (("wheel", wheel_members), ("sdist", sdist_members)):
        stray, unused = _off_list(members, _allowed(kind, version))
        assert not stray, f"the {kind} carries members nothing allows: {stray}"
        assert not unused, f"the {kind} lacks a member these patterns expect: {unused}"


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
        expected = _wheel_name(repo_root)
        assert produced[0].name == expected, (
            f"wheel filename drifted: {produced[0].name!r} != {expected!r}"
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
    """A wheel built straight from the tree holds the package and only the listed members.

    `python -m build --wheel`, like `pip install .` or `pip wheel .`, reads
    the wheel target's settings from the tree; the published wheel is built
    from the sdist (see above). Each path needs its own exclusions, so the
    allow-list is checked on this wheel as well.
    """
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
        stray, unused = _off_list(sorted(names), _allowed("wheel", _version(repo_root)))
        assert not stray, f"the wheel built from the tree carries members nothing allows: {stray}"
        assert not unused, f"the wheel built from the tree lacks expected members: {unused}"
