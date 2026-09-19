#!/usr/bin/env python3
"""Sigantry starter-repo export tool (Plan 14-07 / STARTER-01 closure).

Sigantry's adoption surface ships in two halves:

1. ``templates/starter/`` -- the in-tree, source-of-truth tree that
   captures every adopter-consumed file (parameters.yml, PR templates,
   workflow YAMLs, BRANCHING.md, QUICKSTART.md). This is what CI
   ratifies.
2. The **public** ``sigantry-starter`` GitHub template repo + ADO
   project template -- the artefacts adopters actually click. Per
   CONTEXT.md D-01..D-03 these are mirrored OUT-OF-BAND by an operator
   with the right org admin permissions; sigantry CI never creates a
   sibling repo.

This script's CI-side guarantee is therefore PARITY, not propagation:

    python scripts/export-starter.py --dry-run

walks ``templates/starter/`` and asserts the two parity invariants that
make the operator's mirror step deterministic:

* The ``<!-- pr-checklist:start --> ... <!-- pr-checklist:end -->``
  fenced block is byte-identical between the GitHub PR template, the
  ADO PR template, and ``_partials/pr-checklist.md``.
* Both ``pr-bot.yml`` workflow YAMLs declare the three required
  paths-filter patterns (``**/*.tmdl``, ``**/*.Lakehouse/**``,
  ``**/.platform``).

If either invariant trips, the script exits non-zero with a clear
message in stderr -- the operator's mirror would otherwise propagate
drift into the public template.

The live-mode flags (``--target-github`` / ``--target-ado-org``) raise
``NotImplementedError`` deliberately. Per CONTEXT D-01 the live mirror
is operator-only; see ``.planning/phases/14-starter-repo-pr-review-bot/14-HUMAN-UAT.md``
Test 1 for the documented procedure.

Banned-API discipline: ``yaml.safe_load`` only; no ``httpx`` import.
"""

from __future__ import annotations

import argparse
import difflib
import sys
from pathlib import Path

import yaml

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DEFAULT_ROOT = Path(__file__).resolve().parents[1]
"""Default repo root -- the parent of ``scripts/``."""

STARTER_DIR_RELATIVE = Path("templates") / "starter"

PR_CHECKLIST_FENCE_START = "<!-- pr-checklist:start -->"
PR_CHECKLIST_FENCE_END = "<!-- pr-checklist:end -->"

REQUIRED_WORKFLOW_PATHS_FILTER: tuple[str, ...] = (
    "**/*.tmdl",
    "**/*.Lakehouse/**",
    "**/.platform",
)

GITHUB_PR_TEMPLATE = Path(".github") / "pull_request_template.md"
ADO_PR_TEMPLATE = Path(".azuredevops") / "pull_request_template.md"
PR_CHECKLIST_PARTIAL = Path("_partials") / "pr-checklist.md"

GITHUB_WORKFLOW = Path(".github") / "workflows" / "pr-bot.yml"
ADO_WORKFLOW = Path(".azuredevops") / "jobs" / "pr-bot.yml"


# ---------------------------------------------------------------------------
# Parity helpers
# ---------------------------------------------------------------------------


def _starter_dir(repo_root: Path) -> Path:
    """Resolve the starter dir under ``repo_root``."""
    return repo_root / STARTER_DIR_RELATIVE


def extract_pr_checklist_section(template_path: Path) -> str:
    """Return the PR-checklist fenced section (inclusive of the fence comments).

    Raises :class:`RuntimeError` if either fence is absent so the
    caller can surface a clear "missing fence" error rather than a
    silent string mismatch.
    """
    text = template_path.read_text(encoding="utf-8")
    start = text.find(PR_CHECKLIST_FENCE_START)
    end = text.find(PR_CHECKLIST_FENCE_END)
    if start == -1 or end == -1:
        raise RuntimeError(
            f"PR template {template_path} is missing the required fences "
            f"({PR_CHECKLIST_FENCE_START!r} / {PR_CHECKLIST_FENCE_END!r})."
        )
    return text[start : end + len(PR_CHECKLIST_FENCE_END)]


def assert_pr_template_parity(starter_dir: Path) -> None:
    """Assert byte-identical fenced block across the PR-template trio.

    Raises :class:`RuntimeError` with a unified diff on divergence so
    the operator can see which side drifted.
    """
    gh_section = extract_pr_checklist_section(starter_dir / GITHUB_PR_TEMPLATE)
    ado_section = extract_pr_checklist_section(starter_dir / ADO_PR_TEMPLATE)
    partial = (starter_dir / PR_CHECKLIST_PARTIAL).read_text(encoding="utf-8").strip()

    if gh_section != ado_section:
        diff = "\n".join(
            difflib.unified_diff(
                gh_section.splitlines(),
                ado_section.splitlines(),
                fromfile=str(GITHUB_PR_TEMPLATE),
                tofile=str(ADO_PR_TEMPLATE),
                lineterm="",
            )
        )
        raise RuntimeError("PR templates diverged between .github/ and .azuredevops/:\n" + diff)

    # The partial holds the canonical fenced block -- compare normalised
    # whitespace because the source partial is allowed leading / trailing
    # newlines that the embedded copy would not preserve.
    if gh_section.strip() != partial.strip():
        diff = "\n".join(
            difflib.unified_diff(
                gh_section.splitlines(),
                partial.splitlines(),
                fromfile=".github/pull_request_template.md",
                tofile="_partials/pr-checklist.md",
                lineterm="",
            )
        )
        raise RuntimeError(
            "PR template fenced block diverged from _partials/pr-checklist.md:\n" + diff
        )


def _gha_on_block(doc: object) -> dict[object, object]:
    """Defensive ``on:`` lookup -- PyYAML 1.1 may parse it as Python ``True``."""
    if not isinstance(doc, dict):
        return {}
    if "on" in doc:
        val = doc["on"]
    elif True in doc:
        val = doc[True]
    else:
        return {}
    return val if isinstance(val, dict) else {}


def assert_workflow_pair_parity(starter_dir: Path) -> None:
    """Assert both starter workflow YAMLs reference the required paths-filter.

    The GHA half is fully machine-readable (``on.pull_request.paths``).
    The ADO half is a job template (per RESEARCH Key Finding #8); the
    pipeline-level ``pr.paths.include`` patterns live in adopter-side
    ``azure-pipelines.yml``, but the patterns must still appear in the
    job-template file content (as YAML comments documenting what the
    adopter SHOULD include) so the export script can prove they exist
    on both halves.

    Raises :class:`RuntimeError` with the offending patterns on failure.
    """
    gha_path = starter_dir / GITHUB_WORKFLOW
    ado_path = starter_dir / ADO_WORKFLOW
    if not gha_path.is_file():
        raise RuntimeError(f"missing starter GHA workflow: {gha_path}")
    if not ado_path.is_file():
        raise RuntimeError(f"missing starter ADO job template: {ado_path}")

    # GHA: parse + assert paths-filter machine-readably.
    gha_doc = yaml.safe_load(gha_path.read_text(encoding="utf-8"))
    on_block = _gha_on_block(gha_doc)
    pull_request = on_block.get("pull_request", {}) if isinstance(on_block, dict) else {}
    paths = pull_request.get("paths", []) if isinstance(pull_request, dict) else []
    if not isinstance(paths, list):
        raise RuntimeError(
            f"GHA workflow {gha_path}: on.pull_request.paths must be a list; got {paths!r}"
        )
    missing_gha = [p for p in REQUIRED_WORKFLOW_PATHS_FILTER if p not in paths]
    if missing_gha:
        raise RuntimeError(
            f"GHA workflow paths-filter is missing required pattern(s) {missing_gha!r}; "
            f"got {paths!r}"
        )

    # ADO: file-content scan (pipeline-level trigger lives outside this file).
    ado_text = ado_path.read_text(encoding="utf-8")
    missing_ado = [p for p in REQUIRED_WORKFLOW_PATHS_FILTER if p not in ado_text]
    if missing_ado:
        raise RuntimeError(
            f"ADO job template missing required path pattern(s) {missing_ado!r} "
            f"(expected as YAML comment or trigger-config block in {ado_path})"
        )


# ---------------------------------------------------------------------------
# Top-level commands
# ---------------------------------------------------------------------------


def export_dry_run(repo_root: Path) -> int:
    """Walk ``templates/starter/``; assert parity invariants; return exit code.

    Returns 0 on clean state. On parity failure prints a clear stderr
    message and returns a non-zero exit code (1 = parity drift, 2 =
    structural error such as a missing file).
    """
    starter = _starter_dir(repo_root)
    if not starter.is_dir():
        print(
            f"error: starter directory not found at {starter}",
            file=sys.stderr,
        )
        return 2

    try:
        assert_pr_template_parity(starter)
    except RuntimeError as exc:
        print(f"error: PR-template parity check failed:\n{exc}", file=sys.stderr)
        return 1

    try:
        assert_workflow_pair_parity(starter)
    except RuntimeError as exc:
        print(f"error: workflow-pair parity check failed:\n{exc}", file=sys.stderr)
        return 1

    # Stable, deterministic stdout for idempotency tests.
    print("export-starter: dry-run OK")
    print(f"  starter root:       {starter}")
    print("  pr-template parity: OK")
    print("  workflow parity:    OK")
    return 0


def export_live_to_github(starter_dir: Path, target: str) -> None:
    """Live mirror to a sibling GitHub template repo (operator-only).

    Per CONTEXT.md D-01 the live mirror is forbidden inside CI -- the
    operator runs ``gh repo create sigantry/sigantry-starter --template-public``
    + ``gh repo clone`` + ``cp -r`` + ``git push`` per
    ``.planning/phases/14-starter-repo-pr-review-bot/14-HUMAN-UAT.md``
    Test 1.
    """
    raise NotImplementedError(
        "Live mirror is operator-only per CONTEXT D-01. "
        "See .planning/phases/14-starter-repo-pr-review-bot/14-HUMAN-UAT.md "
        f"Test 1 for the documented procedure (target was {target!r}). "
        f"starter_dir={starter_dir}"
    )


def export_live_to_ado(starter_dir: Path, target_org: str, target_project: str | None) -> None:
    """Live mirror to an ADO project template (operator-only)."""
    raise NotImplementedError(
        "Live mirror is operator-only per CONTEXT D-01. "
        "See .planning/phases/14-starter-repo-pr-review-bot/14-HUMAN-UAT.md "
        f"Test 1 for the documented procedure (target_org={target_org!r}, "
        f"target_project={target_project!r}). starter_dir={starter_dir}"
    )


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Sigantry starter-repo export tool. --dry-run is the default "
            "(CI-safe parity check). Live --target-* flags are operator-only."
        ),
    )
    parser.add_argument(
        "--root",
        type=Path,
        default=DEFAULT_ROOT,
        help="Repo root (defaults to the parent directory of this script).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        default=False,
        help="Walk templates/starter/ and assert parity invariants. Default mode in CI.",
    )
    parser.add_argument(
        "--target-github",
        type=str,
        default=None,
        help=(
            "Target GitHub repo (e.g. 'sigantry/sigantry-starter') for the live "
            "mirror. OPERATOR-ONLY -- raises NotImplementedError inside CI."
        ),
    )
    parser.add_argument(
        "--target-ado-org",
        type=str,
        default=None,
        help=(
            "Target ADO org for the live mirror. OPERATOR-ONLY -- raises "
            "NotImplementedError inside CI."
        ),
    )
    parser.add_argument(
        "--target-ado-project",
        type=str,
        default=None,
        help="Target ADO project name (paired with --target-ado-org).",
    )

    args = parser.parse_args(argv)
    repo_root: Path = args.root.resolve()
    starter = _starter_dir(repo_root)

    # Live-mode flags trip NotImplementedError BEFORE the dry-run path so
    # an operator who mistakenly leaves --target-github set in CI gets a
    # non-zero exit + a clear pointer to 14-HUMAN-UAT.md.
    if args.target_github is not None:
        try:
            export_live_to_github(starter, args.target_github)
        except NotImplementedError as exc:
            print(f"error (operator-only): {exc}", file=sys.stderr)
            return 3
        return 0  # pragma: no cover -- unreachable; the call always raises.

    if args.target_ado_org is not None:
        try:
            export_live_to_ado(starter, args.target_ado_org, args.target_ado_project)
        except NotImplementedError as exc:
            print(f"error (operator-only): {exc}", file=sys.stderr)
            return 3
        return 0  # pragma: no cover -- unreachable; the call always raises.

    # Default mode is dry-run. We treat the absence of --dry-run as
    # implicit-dry-run too -- the script's whole CI guarantee is the
    # parity check.
    return export_dry_run(repo_root)


if __name__ == "__main__":
    sys.exit(main())
