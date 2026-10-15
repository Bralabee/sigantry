"""Install instructions in the documentation name the published distribution.

#17. The distribution on PyPI is ``sigantry``, versioned on the public SemVer
line (1.x). Older documentation told readers to ``pip install sigantry-core``
(no installable release) and to pin ``>=3.0`` (a version scheme that was never
published), so an instruction could be wrong in its name, its version, or both.

The rule, for ``docs/`` plus ``README.md`` and ``CONTRIBUTING.md``:

* every ``sigantry...`` argument of a ``pip install`` command is ``sigantry``,
  optionally with extras, and either unpinned, pinned ``>=1.N,<2``, or pinned
  to a placeholder (``==<version>``, ``==X.Y.Z``) that a runbook explains;
* every ``sigantry...`` requirement with a version specifier anywhere in the
  text (``dependencies = ["sigantry>=1.0,<2"]``) follows the same rule.

The decision records that explain the history quote the old instructions on
purpose (ADR-0010, ADR-0011, ADR-0016, ADR-0017) and are exempt by path.
"""

from __future__ import annotations

import pathlib
import re

_DOC_PREFIXES: tuple[str, ...] = ("docs/",)
_DOC_FILES: frozenset[str] = frozenset({"README.md", "CONTRIBUTING.md"})

# Decision records that quote the superseded instructions as history.
_HISTORY_EXEMPT: frozenset[str] = frozenset(
    {
        "docs/decisions/ADR-0010-commercial-model.md",
        "docs/decisions/ADR-0011-rename-to-sigantry.md",
        "docs/decisions/ADR-0016-plugin-scope-and-parity-model.md",
        "docs/decisions/ADR-0017-distribution-name-sigantry.md",
    }
)

# One ``pip install`` command, up to a comment or the next shell command.
_INSTALL = re.compile(r"\b(?:pip3?|pipx|uv\s+pip)\s+install\b([^\n#;&|]*)", re.IGNORECASE)

# A requirement on a sigantry-ish name followed by a version specifier. The
# operator must be followed by a version, so ``sigantry <verb>`` and
# ``SIGANTRY_<SECTION>`` placeholders are not read as requirements.
_OP = r"(?:===|==|>=|<=|~=|!=|<|>)"
_VER = r"(?:\d[\w.*+!-]*|<version>|X\.Y\.Z)"
_SPECIFIED = re.compile(
    rf"(?<![\w./\\<-])(sigantry[\w.-]*(?:\[[^\]\n]*\])?\s*{_OP}\s*{_VER}"
    rf"(?:\s*,\s*{_OP}\s*{_VER})*)",
    re.IGNORECASE,
)

# What an install argument or requirement may say.
_ALLOWED = re.compile(
    r"sigantry(?:\[[a-z0-9_,-]+\])?(?:>=1(?:\.\d+){0,2},<2|==<version>|==X\.Y\.Z)?",
)


def _is_doc(rel: str) -> bool:
    return (rel.startswith(_DOC_PREFIXES) and rel.endswith(".md")) or rel in _DOC_FILES


def _bad_requirements(line: str) -> list[str]:
    """Return every sigantry requirement on ``line`` that breaks the rule."""
    bad: list[str] = []
    # In prose, a command sits inside a code span; reading past the span's
    # closing backtick would take the next sentence's words as arguments.
    spans = re.findall(r"`([^`]*)`", line) if "`" in line else [line]
    for match in (m for span in spans for m in _INSTALL.finditer(span)):
        for arg in match.group(1).split():
            arg = arg.strip("`\"'(),.:;")
            if arg.lower().startswith("sigantry") and not _ALLOWED.fullmatch(arg):
                bad.append(arg)
    for match in _SPECIFIED.finditer(line):
        req = re.sub(r"\s+", "", match.group(1))
        if not _ALLOWED.fullmatch(req):
            bad.append(req)
    return sorted(set(bad))


def test_docs_install_lines_name_the_published_distribution(
    repo_files: tuple[tuple[pathlib.Path, str], ...],
) -> None:
    """Every documented install of sigantry would install the 1.x release."""
    offenders: list[str] = []
    install_lines = 0
    for path, rel in repo_files:
        if not _is_doc(rel) or rel in _HISTORY_EXEMPT:
            continue
        text = path.read_text(encoding="utf-8")
        for lineno, line in enumerate(text.splitlines(), 1):
            if _INSTALL.search(line):
                install_lines += 1
            for req in _bad_requirements(line):
                offenders.append(f"{rel}:{lineno}: {req!r} in: {line.strip()}")

    # The documentation has dozens of install lines. Reading none means the
    # inventory or the pattern is broken, not that the docs are clean.
    assert install_lines >= 20, f"only {install_lines} install lines found; the scan is broken"
    assert not offenders, (
        "install instructions must name 'sigantry', unpinned or pinned '>=1.N,<2' "
        "(ADR-0017):\n" + "\n".join(offenders)
    )


def test_the_history_exemptions_still_exist(repo_root: pathlib.Path) -> None:
    """An exemption for a file that was renamed or deleted excuses nothing and hides drift."""
    missing = sorted(rel for rel in _HISTORY_EXEMPT if not (repo_root / rel).is_file())
    assert not missing, f"exempt decision records no longer exist: {missing}"


def test_the_install_rule_fires_and_spares_correct_lines() -> None:
    """Fail-direction arm over the spellings the rule exists to refuse."""
    refused = [
        "pip install sigantry-core",
        "pip install sigantry_core",
        "pip install 'sigantry>=3.0'",
        "pip install sigantry==3.4.1",
        "pip install --upgrade Sigantry-Core",
        'dependencies = ["sigantry>=3.0,<4"]',
        "sigantry-core>=3.0",
        "uv pip install sigantry~=1.1",
    ]
    accepted = [
        "pip install sigantry",
        "pip install --upgrade sigantry",
        "pip install 'sigantry[dev]'",
        'dependencies = ["sigantry>=1.0,<2"]',
        "Pins `pip install sigantry==<version>` in every stage",
        "`pip install sigantry==X.Y.Z` resolves in a clean environment.",
        'pip install -e ".[dev,test]"',
        "pip install /path/to/sigantry-<version>-py3-none-any.whl",
        "run: pip install sigantry  # or install from your checkout",
        "activate the environment you ran `pip install sigantry` in",
    ]
    assert [line for line in refused if not _bad_requirements(line)] == []
    assert {line: _bad_requirements(line) for line in accepted if _bad_requirements(line)} == {}
