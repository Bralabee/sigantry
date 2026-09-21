"""The shipped surface must not name a distribution that does not exist.

STRUCT-01. The project publishes to PyPI as ``sigantry``; ``sigantry-core``
was the pre-v1.0 plan and **404s** (ADR-0017 records the amendment). Shipped
consumer templates, this repo's own workflows and the packaged code were still
telling people to ``pip install sigantry-core``, which fails for every
consumer who follows them.

Scope note: this guard covers the surface that EXECUTES or ships — templates,
workflows, scripts and the package itself. Prose in ``docs/`` is handled
separately; several of those references are deliberate, because they explain
that ``sigantry-core`` does not resolve, and a blanket ban would delete the
explanation along with the defect.

The inventory comes from the git index via the ``repo_files`` fixture, so the
guard polices repository content rather than a developer's working tree.
"""

from __future__ import annotations

import pathlib
import tomllib

# The distribution name that does not exist on PyPI.
_DEAD_DIST = "sigantry-core"

# Trees that ship to a consumer or execute in CI.
_SHIPPED_PREFIXES: tuple[str, ...] = (
    "templates/",
    ".github/workflows/",
    "scripts/",
    "sigantry_core/",
)
_SHIPPED_FILES: frozenset[str] = frozenset({".pre-commit-config.yaml"})

# Azure DevOps pipeline-artifact identifier, not a package name. Renaming it
# is a breaking change for any consumer pipeline that downloads the artifact
# by name, so it needs a deprecation window rather than a find-and-replace.
_ALLOWED_TOKENS: tuple[str, ...] = ("sigantry-core-wheel",)


def _is_shipped(rel: str) -> bool:
    return rel.startswith(_SHIPPED_PREFIXES) or rel in _SHIPPED_FILES


def _strip_allowed(text: str) -> str:
    for token in _ALLOWED_TOKENS:
        text = text.replace(token, "")
    return text


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
    """No template, workflow, script or packaged file names ``sigantry-core``."""
    offenders: list[str] = []
    scanned = 0
    for path, rel in repo_files:
        if not _is_shipped(rel):
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        scanned += 1
        if _DEAD_DIST in _strip_allowed(text):
            for lineno, line in enumerate(text.splitlines(), 1):
                if _DEAD_DIST in _strip_allowed(line):
                    offenders.append(f"{rel}:{lineno}: {line.strip()}")

    # An empty scan would pass this test having read nothing. The shipped
    # trees are not empty, so refuse that answer rather than return it.
    assert scanned > 0, "no shipped files were scanned; the inventory or the prefixes are wrong"

    assert not offenders, (
        f"{_DEAD_DIST!r} does not exist on PyPI (the distribution is 'sigantry'), "
        "so these shipped references fail for every consumer that follows them:\n"
        + "\n".join(offenders)
    )


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
