"""Audit-2026-05-07 W3.2 -- meta-gate: every ``uses:`` reference in
``.github/workflows/*.yml`` is pinned to a 40-char SHA.

Pre-W3.2 the workflows referenced actions by version tag
(``actions/checkout@v4``). Tag references are mutable -- an attacker
who compromised the action's repo could push a malicious commit and
re-tag, and every CI run would silently pull the malicious version.
SHA-pinning makes the reference immutable. Dependabot
(``.github/dependabot.yml``, also added in W3.2) rewrites the SHAs +
trailing version comments weekly so the pin stays current with
upstream patches.

The expected pin format::

    uses: <owner>/<repo>@<40-char SHA>  # v<N>

The trailing ``# v<N>`` comment is the human-readable version; the
SHA before the ``#`` is the actual pin. This test pins both.

Falsifiability: this test FAILS against any commit that re-introduces
a tag-suffix ``@v<N>`` reference.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
WORKFLOWS_DIR = REPO_ROOT / ".github" / "workflows"

# Match ``uses: owner/repo@<ref>`` (anywhere on a line). ``ref`` may be
# a 40-char SHA, a tag (``v4``), or a branch name. We allow trailing
# content (the ``# v4`` comment).
_USES_PATTERN = re.compile(
    r"^\s*-?\s*uses:\s*"
    r"(?P<repo>[a-zA-Z0-9_.\-]+/[a-zA-Z0-9_.\-]+)"
    r"@(?P<ref>[a-zA-Z0-9_.\-]+)"
    r"(?:\s+#\s*(?P<comment>.+))?\s*$"
)

_SHA_PATTERN = re.compile(r"^[0-9a-f]{40}$")


def _scan_uses() -> list[tuple[Path, int, str, str, str | None]]:
    """Return ``(path, lineno, repo, ref, comment)`` for every uses-line."""
    findings: list[tuple[Path, int, str, str, str | None]] = []
    for path in sorted(WORKFLOWS_DIR.glob("*.yml")):
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            m = _USES_PATTERN.match(line)
            if m:
                findings.append(
                    (
                        path,
                        lineno,
                        m.group("repo"),
                        m.group("ref"),
                        m.group("comment"),
                    )
                )
    return findings


def test_every_uses_pin_is_a_40_char_sha() -> None:
    """Every action reference points at a 40-char commit SHA.

    Falsifiability: a workflow that uses ``actions/checkout@v4``
    instead of the SHA fails this test.
    """
    offenders: list[str] = []
    for path, lineno, repo, ref, _comment in _scan_uses():
        if not _SHA_PATTERN.fullmatch(ref):
            rel = path.relative_to(REPO_ROOT)
            offenders.append(f"{rel}:{lineno}  {repo}@{ref}  (not a 40-char SHA)")
    assert offenders == [], (
        "Tag-suffix action references found in .github/workflows/. "
        "Pin to a 40-char commit SHA so tag mutation cannot inject a "
        "malicious release. Use ``gh api repos/<owner>/<repo>/git/refs/tags/<tag>`` "
        "to resolve, then dependabot will keep them current.\n  - " + "\n  - ".join(offenders)
    )


def test_every_uses_pin_carries_version_comment() -> None:
    """Every SHA-pinned action carries a ``# v<N>`` comment for human readability.

    Without the comment the workflow is unreadable to operators (40-char
    hex strings carry no version information). Dependabot rewrites the
    comment alongside the SHA on every upgrade so this stays accurate.
    """
    offenders: list[str] = []
    for path, lineno, repo, ref, comment in _scan_uses():
        if not _SHA_PATTERN.fullmatch(ref):
            continue  # already flagged by the SHA-pin test above
        if not comment:
            rel = path.relative_to(REPO_ROOT)
            offenders.append(f"{rel}:{lineno}  {repo}@{ref}  (no `# v<N>` comment)")
    assert offenders == [], (
        "SHA-pinned actions missing the trailing `# v<N>` version "
        "comment. Operators cannot read 40-char hex strings.\n  - " + "\n  - ".join(offenders)
    )


def test_dependabot_config_exists_and_covers_actions() -> None:
    """``.github/dependabot.yml`` exists with a ``github-actions`` ecosystem entry.

    Without dependabot SHA pins become frozen-in-time security risks --
    they're stable but drift behind upstream patches. Dependabot bumps
    them weekly.
    """
    path = REPO_ROOT / ".github" / "dependabot.yml"
    assert path.is_file(), (
        ".github/dependabot.yml is missing. SHA-pinning without auto-bump "
        "leaves the pins frozen behind upstream patches."
    )
    text = path.read_text(encoding="utf-8")
    assert (
        "package-ecosystem: github-actions" in text or 'package-ecosystem: "github-actions"' in text
    ), "dependabot.yml does not declare a github-actions ecosystem entry."


@pytest.mark.parametrize(
    "expected_repo",
    [
        "actions/checkout",
        "actions/setup-python",
    ],
)
def test_known_pinned_actions_resolve_consistently(expected_repo: str) -> None:
    """Every reference to a known action uses the SAME SHA across workflows.

    If two workflows pin ``actions/checkout`` to different SHAs, that's
    a drift signal that dependabot left half the workflows stale or
    that an operator hand-bumped one without the others.
    """
    refs = {ref for _path, _line, repo, ref, _c in _scan_uses() if repo == expected_repo}
    assert len(refs) <= 1, (
        f"{expected_repo} pinned to multiple SHAs across workflows: "
        f"{sorted(refs)}. Dependabot should keep them aligned -- "
        "manually bumping one without the others is the usual cause."
    )
