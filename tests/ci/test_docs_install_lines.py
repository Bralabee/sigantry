"""Install instructions in the documentation name the published distribution.

#17. The distribution on PyPI is ``sigantry``, versioned on the public SemVer
line (1.x). Older documentation told readers to ``pip install sigantry-core``
(no installable release) and to pin ``>=3.0`` (a version scheme that was never
published), so an instruction could be wrong in its name, its version, or both.

The rule, for the Markdown under ``docs/`` and ``templates/`` (the starter
and demo scaffolds' own guides) plus ``README.md`` and ``CONTRIBUTING.md``:

* every ``sigantry...`` argument of a ``pip install`` command is ``sigantry``,
  optionally with extras, and either unpinned, pinned ``>=1.N,<2``, or pinned
  to a placeholder (``==<version>``, ``==X.Y.Z``) that a runbook explains;
* every ``sigantry...`` requirement with a version specifier anywhere in the
  text (``dependencies = ["sigantry>=1.0,<2"]``) follows the same rule.

The decision records that explain the history quote the old instructions on
purpose (ADR-0010, ADR-0011, ADR-0016, ADR-0017) and are exempt by path.

A command continued with a trailing backslash is read as one line. In prose,
a code span that holds its own install command is read on its own; any other
code span is read as part of the sentence around it.
"""

from __future__ import annotations

import pathlib
import re

_DOC_PREFIXES: tuple[str, ...] = ("docs/", "templates/")
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

# One ``pip install`` command, up to a comment, the next shell command, or
# the end of the code span it was found in (marked NUL by ``_unwrap``).
_INSTALL = re.compile(r"\b(?:pip3?|pipx|uv\s+pip)\s+install\b([^\n#;&|\x00]*)", re.IGNORECASE)

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
    r"sigantry(?:\[[a-z0-9_,-]+\])?(?:>=1(?:\.\d+){0,2},<2(?:\.0){0,2}|==<version>|==X\.Y\.Z)?",
)


def _is_doc(rel: str) -> bool:
    return (rel.startswith(_DOC_PREFIXES) and rel.endswith(".md")) or rel in _DOC_FILES


def _logical_lines(text: str) -> list[tuple[int, str]]:
    """Return ``(first line number, line)`` with backslash continuations joined."""
    lines: list[tuple[int, str]] = []
    start, parts = 0, []
    for lineno, line in enumerate(text.splitlines(), 1):
        if not parts:
            start = lineno
        if line.rstrip().endswith("\\"):
            parts.append(line.rstrip()[:-1])
            continue
        parts.append(line)
        lines.append((start, " ".join(parts)))
        parts = []
    if parts:
        lines.append((start, " ".join(parts)))
    return lines


def _install_texts(line: str) -> list[str]:
    """The texts to search for install commands on one (logical) line.

    A code span holding its own install command is read alone, and ends that
    command: ``ran `pip install sigantry` in`` must not take ``in`` as an
    argument. Every other code span is unwrapped into the sentence, so
    ``pip install `sigantry-core` `` and an install command written outside a
    span on a line that also has spans are both read.
    """
    own = [span for span in re.findall(r"`([^`]*)`", line) if _INSTALL.search(span)]
    rest = re.sub(
        r"`([^`]*)`", lambda m: "\x00" if _INSTALL.search(m.group(1)) else m.group(1), line
    )
    return [*own, rest]


def _bad_requirements(line: str) -> list[str]:
    """Return every sigantry requirement on ``line`` that breaks the rule."""
    bad: list[str] = []
    for match in (m for text in _install_texts(line) for m in _INSTALL.finditer(text)):
        # ``'sigantry>=1.1, <2'`` is one requirement, not two arguments.
        for arg in re.sub(r"(\d)\s*,\s*(?=[<>=!~])", r"\1,", match.group(1)).split():
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
        for lineno, line in _logical_lines(text):
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
        "Run pip install sigantry-core, then `sigantry --help`.",
        "pip install sigantry-core  # see `docs`",
        "pip install `sigantry-core`",
        "pip install `sigantry` then pip install sigantry==3.0",
        "pip install sigantry-core, then sigantry --help",
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
        "`pip install sigantry` from PyPI. The import package is `sigantry_core`.",
        "pip install 'sigantry>=1.1, <2'",
        "pip install sigantry, then sigantry deploy run",
        'dependencies = ["sigantry>=1.1.0,<2.0"]',
    ]
    assert [line for line in refused if not _bad_requirements(line)] == []
    assert {line: _bad_requirements(line) for line in accepted if _bad_requirements(line)} == {}


def test_a_continued_command_is_read_as_one_line() -> None:
    """Fail-direction arm: a backslash continuation does not hide the package."""
    text = "```bash\npip install --upgrade \\\n    sigantry-core\n```\n"
    hits = [(n, _bad_requirements(line)) for n, line in _logical_lines(text)]
    assert (2, ["sigantry-core"]) in hits
