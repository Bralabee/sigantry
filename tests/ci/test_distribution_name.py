"""The shipped surface must not name a distribution that does not exist.

STRUCT-01. The project publishes to PyPI as ``sigantry``; ``sigantry-core``
was the pre-v1.0 plan and holds no installable release (ADR-0017 records the
amendment). Shipped consumer templates, this repo's own workflows and the
packaged code were still telling people to ``pip install sigantry-core``,
which fails for every consumer who follows them.

Scope note: this guard covers the surface that EXECUTES or ships -- templates,
workflows, scripts, the package itself, and the root files a consumer reads or
installs from (``pyproject.toml``, ``environment.yml``, ``README.md``,
``CONTRIBUTING.md``). Prose in ``docs/`` is covered by
``test_docs_install_lines.py``, which checks install instructions there
without deleting the ADRs that explain why ``sigantry-core`` does not resolve.

Spellings. pip normalises ``-``, ``_`` and ``.`` and ignores case, so
``sigantry_core``, ``Sigantry-Core`` and ``sigantry.core`` all name the same
dead distribution. The hyphen and dot spellings are never anything else and
are refused anywhere. ``sigantry_core`` is also the IMPORT name, so the
underscore spelling is refused only where it is used as a distribution: as a
``pip install`` argument, or followed by a version specifier. A bare
``"sigantry_core"`` inside a TOML or YAML dependency list is not recognised
(it is indistinguishable by text from the import name in tool configuration).

The inventory comes from the ``repo_files`` fixture: ``git ls-files`` of the
tracked files plus untracked files that are not ignored, so a violation in a
file created but not yet staged is still caught.
"""

from __future__ import annotations

import pathlib
import re
import tomllib

from .test_docs_install_lines import _logical_lines

# The distribution name that does not exist on PyPI, in the spellings that
# are never the import name: any case, ``-`` or ``.`` as separator.
_DEAD_DIST = re.compile(r"sigantry[-.]core", re.IGNORECASE)

# The underscore spelling, only where it names a distribution.
_DEAD_DIST_AS_REQUIREMENT: tuple[re.Pattern[str], ...] = (
    # ``pip install ... sigantry_core`` up to the end of that command.
    re.compile(
        r"\b(?:pip3?|pipx|uv\s+pip)\s+install\b[^\n#;&|]*?(?<![\w./\\-])sigantry_core(?![\w.-])",
        re.IGNORECASE,
    ),
    # ``sigantry_core>=1.0``, ``sigantry_core[extra]==1.0`` and the like.
    re.compile(
        r"(?<![\w./\\-])sigantry_core\s*(?:\[[^\]\n]*\])?\s*(?:===|==|>=|<=|~=|!=|[<>]\s*\d)",
        re.IGNORECASE,
    ),
)

# Trees that ship to a consumer or execute in CI.
_SHIPPED_PREFIXES: tuple[str, ...] = (
    "templates/",
    ".github/workflows/",
    "scripts/",
    "sigantry_core/",
)
_SHIPPED_FILES: frozenset[str] = frozenset(
    {
        ".pre-commit-config.yaml",
        "pyproject.toml",
        "environment.yml",
        "README.md",
        "CONTRIBUTING.md",
    }
)

# Azure DevOps pipeline-artifact identifier, not a package name. Renaming it
# is a breaking change for any consumer pipeline that downloads the artifact
# by name, so it needs a deprecation window rather than a find-and-replace.
_ALLOWED_TOKENS: tuple[str, ...] = ("sigantry-core-wheel",)
_ALLOWED_RE = re.compile("|".join(map(re.escape, _ALLOWED_TOKENS)), re.IGNORECASE)


def _is_shipped(rel: str) -> bool:
    return rel.startswith(_SHIPPED_PREFIXES) or rel in _SHIPPED_FILES


def _strip_allowed(text: str) -> str:
    return _ALLOWED_RE.sub("", text)


def _names_dead_dist(line: str) -> bool:
    line = _strip_allowed(line)
    return bool(_DEAD_DIST.search(line)) or any(
        pattern.search(line) for pattern in _DEAD_DIST_AS_REQUIREMENT
    )


def test_declared_distribution_name_is_sigantry(repo_root: pathlib.Path) -> None:
    """Precondition: the premise of this guard still holds.

    If the project ever renames its distribution again, this fails first and
    points at the rule below, instead of the rule quietly policing a name that
    is no longer the right one.
    """
    pyproject = tomllib.loads((repo_root / "pyproject.toml").read_text(encoding="utf-8"))
    assert pyproject["project"]["name"] == "sigantry"


def test_shipped_surface_does_not_reference_the_dead_distribution(
    repo_files: tuple[tuple[pathlib.Path, str], ...],
) -> None:
    """No shipped file names ``sigantry-core`` in any spelling pip accepts."""
    offenders: list[str] = []
    scanned: set[str] = set()
    for path, rel in repo_files:
        if not _is_shipped(rel):
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        scanned.add(rel)
        for lineno, line in _logical_lines(text):
            if _names_dead_dist(line):
                offenders.append(f"{rel}:{lineno}: {line.strip()}")

    # An empty scan would pass this test having read nothing, and a root file
    # dropped from the inventory would leave its scope silently unchecked.
    missing = sorted(_SHIPPED_FILES - scanned)
    assert not missing, f"shipped root files were not scanned: {missing}"
    assert scanned - _SHIPPED_FILES, "no shipped tree was scanned; the prefixes are wrong"

    assert not offenders, (
        "'sigantry-core' has no installable release on PyPI (the distribution is "
        "'sigantry'), so these shipped references fail for every consumer that "
        "follows them:\n" + "\n".join(offenders)
    )


def test_the_dead_distribution_patterns_fire_and_spare_the_import_name() -> None:
    """Fail-direction arm: every spelling pip accepts is caught; imports are not."""
    caught = [
        "pip install sigantry-core",
        "pip install Sigantry-Core==1.0",
        "pip install sigantry.core",
        "pip install sigantry_core",
        "python -m pip install --upgrade SIGANTRY_CORE",
        "uv pip install sigantry_core[dev]",
        '  "sigantry_core>=1.0,<2",',
        "sigantry_core==1.1.0",
        "sigantry_core[test] ~= 1.1",
        " ".join(line for _, line in _logical_lines("pip install \\\n  sigantry_core\n")),
    ]
    spared = [
        "import sigantry_core",
        "python -m sigantry_core.cli --help",
        'sigantry = "sigantry_core.cli:main"',
        "mypy sigantry_core/ scripts/",
        "SIGANTRY_CORE__TENANT_ID=abc",
        "pip install -e . && python -c 'import sigantry_core'",
        "pip install sigantry",
        "artifact: 'sigantry-core-wheel'",
        "artifact: 'SIGANTRY-CORE-WHEEL'",
        "cp -r src/sigantry_core==old .",
    ]
    assert [line for line in caught if not _names_dead_dist(line)] == []
    assert [line for line in spared if _names_dead_dist(line)] == []


def test_the_allowlisted_token_is_still_in_use(
    repo_files: tuple[tuple[pathlib.Path, str], ...],
) -> None:
    """The carve-out is live, not dead weight hiding a future regression.

    Without this, ``sigantry-core-wheel`` could be removed from the templates
    and the allowlist would silently go on excusing any future occurrence.
    """
    found = any(
        _is_shipped(rel)
        and "sigantry-core-wheel" in path.read_text(encoding="utf-8", errors="ignore")
        for path, rel in repo_files
    )
    assert found, (
        "allowlisted token 'sigantry-core-wheel' no longer appears; drop it from the allowlist"
    )
