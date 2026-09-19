"""Starter dual-CI parity tests (Plan 14-06 Task 3 / STARTER-07 workflow half).

Mirrors the Phase 12 / Phase 13 pattern (``test_pipeline_template_parity.py``,
``test_drift_template_parity.py``). The starter pair is NOT covered by
``scripts/ci/check-dual-ci-parity.py`` (which scans the monorepo's
``templates/{stages,schedules,pr-review}/`` and ``.github/workflows/``);
instead this test file does its own paths-filter + invocation-arg
parity checks over the starter workflow pair under
``templates/starter/.github/workflows/`` and
``templates/starter/.azuredevops/jobs/``.

Three tests, replacing all three Wave 0 ``pytest.xfail`` stubs:

1. ``test_starter_template_pair_exists``                -- both files exist.
2. ``test_starter_pair_paths_filter_includes_tmdl_lakehouse_platform``
                                                       -- 3 path patterns appear.
3. ``test_starter_pair_invokes_pr_bot_with_same_arg_surface``
                                                       -- identical flag set
                                                          (modulo CI-system
                                                          variable spelling).

T-14-06-04 mitigation: ``yaml.safe_load`` only; banned-API gate enforced.
"""

from __future__ import annotations

import pathlib
import re
from typing import Any

import yaml

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
GHA_FILE = REPO_ROOT / "templates" / "starter" / ".github" / "workflows" / "pr-bot.yml"
ADO_FILE = REPO_ROOT / "templates" / "starter" / ".azuredevops" / "jobs" / "pr-bot.yml"

_REQUIRED_PATH_PATTERNS = ("**/*.tmdl", "**/*.Lakehouse/**", "**/.platform")


def _gha_on(doc: dict[Any, Any]) -> dict[Any, Any]:
    """Resolve the GHA workflow's ``on:`` block defensively against YAML 1.1.

    PyYAML 1.1 (project default) parses unquoted ``on:`` as Python
    ``True``; mirror the lookup discipline from
    ``scripts/ci/check-dual-ci-parity.py:_gha_on``.
    """
    if not isinstance(doc, dict):
        return {}
    if "on" in doc:
        val = doc["on"]
    elif True in doc:
        val = doc[True]
    else:
        return {}
    return val if isinstance(val, dict) else {}


# ---------------------------------------------------------------------------
# Test 1 -- pair exists
# ---------------------------------------------------------------------------


def test_starter_template_pair_exists() -> None:
    """Both halves of the starter pr-bot pair exist on disk (Plan 14-06)."""
    assert GHA_FILE.is_file(), f"missing: {GHA_FILE}"
    assert ADO_FILE.is_file(), f"missing: {ADO_FILE}"


# ---------------------------------------------------------------------------
# Test 2 -- paths-filter parity
# ---------------------------------------------------------------------------


def test_starter_pair_paths_filter_includes_tmdl_lakehouse_platform() -> None:
    """Both YAMLs reference the three required path patterns (D-19 / RESEARCH Key Finding #8).

    The GHA workflow declares the patterns in ``on.pull_request.paths``
    (machine-readable). The ADO file is a JOB TEMPLATE (per RESEARCH Key
    Finding #8); the patterns live in the file content as either YAML
    comments documenting the adopter's pipeline-level ``pr.paths.include``
    OR as machine-readable trigger config. The test asserts the GHA side
    parses cleanly AND the ADO file's text contains all three patterns.
    """
    # GHA: parse via yaml.safe_load and assert the paths array is exact.
    gha_doc = yaml.safe_load(GHA_FILE.read_text("utf-8"))
    on_block = _gha_on(gha_doc)
    pull_request = on_block.get("pull_request", {})
    assert isinstance(pull_request, dict), (
        f"GHA workflow's on.pull_request must be a dict; got {pull_request!r}"
    )
    paths = pull_request.get("paths", [])
    assert isinstance(paths, list), (
        f"GHA workflow's on.pull_request.paths must be a list; got {paths!r}"
    )
    for pattern in _REQUIRED_PATH_PATTERNS:
        assert pattern in paths, (
            f"GHA workflow's paths-filter missing required pattern {pattern!r}; got {paths!r}"
        )

    # ADO: file-content scan (job template; pipeline-level trigger lives
    # in the adopter's azure-pipelines.yml). Per RESEARCH Key Finding #8
    # the patterns must appear in the file text whether as YAML comments,
    # documentation, or trigger config.
    ado_text = ADO_FILE.read_text("utf-8")
    for pattern in _REQUIRED_PATH_PATTERNS:
        assert pattern in ado_text, (
            f"ADO job template missing required path pattern {pattern!r} in "
            f"its file content (expected as comment or trigger config)"
        )


# ---------------------------------------------------------------------------
# Test 3 -- invocation-arg parity
# ---------------------------------------------------------------------------


_VAR_GHA_RE = re.compile(r"\$\{\{[^}]*\}\}")
_VAR_ADO_RE = re.compile(r"\$\([^)]+\)")
_VAR_SHELL_RE = re.compile(r"\$[A-Z_][A-Z0-9_]*")


def _normalise_ci_vars(line: str) -> str:
    """Replace GHA ``${{ ... }}``, ADO ``$(...)`` / ``${{ parameters.* }}``,
    and POSIX shell ``$VAR`` references with the placeholder ``<VAR>`` so
    flag-and-value sequences from both workflows compare cleanly.
    """
    line = _VAR_GHA_RE.sub("<VAR>", line)
    line = _VAR_ADO_RE.sub("<VAR>", line)
    line = _VAR_SHELL_RE.sub("<VAR>", line)
    return line


def _extract_pr_bot_invocation_flags(text: str) -> list[str]:
    """Extract the flag-and-value sequence from the ``sigantry pr-bot run`` invocation.

    Walks the file text line-by-line, normalises CI-system variable
    spelling, and returns the list of flags-and-values that follow
    ``sigantry pr-bot run``. Trailing-backslash-continued lines are
    consumed in order so the sequence is preserved.

    Returns a list like ``['--provider', '<VAR>', '--pr-id', '<VAR>', ...]``.
    """
    lines = text.splitlines()
    inv_idx: int | None = None
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("sigantry pr-bot run"):
            inv_idx = i
            break
    assert inv_idx is not None, "sigantry pr-bot run invocation not found in file"

    # Collect the invocation line plus any continuation lines (\-suffixed).
    collected: list[str] = []
    j = inv_idx
    while j < len(lines):
        raw = lines[j]
        if raw.rstrip().endswith("\\"):
            collected.append(raw.rstrip()[:-1])
        else:
            collected.append(raw)
            break
        j += 1
    cmd = " ".join(s.strip() for s in collected)
    cmd = _normalise_ci_vars(cmd)

    # Strip leading 'sigantry pr-bot run'.
    cmd = cmd.split("sigantry pr-bot run", 1)[1].strip()

    # Tokenise on whitespace; strip stray quotes.
    tokens = []
    for tok in cmd.split():
        tok = tok.strip().strip('"').strip("'")
        if tok:
            tokens.append(tok)
    return tokens


def test_starter_pair_invokes_pr_bot_with_same_arg_surface() -> None:
    """Both workflows invoke ``sigantry pr-bot run`` with identical flag-and-value sequences.

    CI-system variable spelling (``${{ ... }}`` vs ``$(...)``) is
    normalised to the placeholder ``<VAR>`` before comparison.
    """
    gha_tokens = _extract_pr_bot_invocation_flags(GHA_FILE.read_text("utf-8"))
    ado_tokens = _extract_pr_bot_invocation_flags(ADO_FILE.read_text("utf-8"))

    assert gha_tokens == ado_tokens, (
        "Starter pair invocation-arg drift: GHA + ADO 'sigantry pr-bot run' "
        f"flag sequences diverged.\n"
        f"GHA: {gha_tokens!r}\n"
        f"ADO: {ado_tokens!r}"
    )

    # Also assert the canonical flag set is present (regression catcher
    # against accidentally dropping a required flag from BOTH files).
    expected_flags = {
        "--provider",
        "--pr-id",
        "--token",
        "--base-dir",
        "--head-dir",
    }
    gha_flag_names = {t for t in gha_tokens if t.startswith("--")}
    assert expected_flags.issubset(gha_flag_names), (
        f"GHA invocation missing canonical flag(s): {expected_flags - gha_flag_names!r}"
    )
