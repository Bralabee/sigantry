#!/usr/bin/env python3
"""Sigantry demo-repo export tool (Plan 15-01 / DEMO-01 closure CI half).

Byte-extension of scripts/export-starter.py. Sigantry's public-demo
surface ships in two halves:

1. ``templates/demo/`` -- the in-tree, source-of-truth tree that
   captures every adopter-consumed file (parameters.yml, sync.yml,
   fabric_items/, README, docs, PR templates, workflow YAMLs).
   This is what CI ratifies.
2. The **public** ``demo-sigantry`` GitHub repo + ADO project -- the
   artefacts adopters actually click. Per CONTEXT.md D-01 these are
   mirrored OUT-OF-BAND by an operator with the right org admin
   permissions; sigantry CI never creates a sibling repo.

This script's CI-side guarantee is therefore PARITY, not propagation:

    python scripts/export-demo.py --dry-run

walks ``templates/demo/`` and asserts the four parity invariants
that make the operator's mirror step deterministic:

  Invariant 1 (inherited from starter):
    PR-checklist fenced block byte-identical across the 3 fence
    sources.
  Invariant 2 (inherited from starter):
    Both pr-bot.yml workflows declare the 3 required path
    patterns.
  Invariant 3 (demo-specific):
    templates/demo/parameters.yml validates clean via
    sigantry_core.deploy.parameters.
  Invariant 4 (demo-specific):
    The 4 fabric_items/<*>/.platform files carry the locked
    placeholder logicalIds (d001..d004) and metadata.type matches
    the directory suffix.

If any invariant trips, the script exits non-zero with a clear
error message. Live flags (``--target-github``, ``--target-ado-org``,
``--target-ado-project``) raise NotImplementedError per CONTEXT D-01.

Banned-API discipline: ``yaml.safe_load`` only; no ``httpx`` import.
"""

from __future__ import annotations

import argparse
import difflib
import json
import os
import sys
from pathlib import Path

import yaml

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DEFAULT_ROOT = Path(__file__).resolve().parents[1]
"""Default repo root -- the parent of ``scripts/``."""

DEMO_DIR_RELATIVE = Path("templates") / "demo"

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

# Demo-specific constants (Invariant 3 + 4).
DEMO_PARAMETERS_YML = Path("parameters.yml")
DEMO_FABRIC_ITEMS_DIRNAME = "fabric_items"

# Per CONTEXT D-03: locked placeholder logicalIds for the 4 sample items.
EXPECTED_LOGICAL_IDS: dict[str, str] = {
    "Sales.Lakehouse": "00000000-0000-0000-0000-00000000d001",
    "LoadOrders.Notebook": "00000000-0000-0000-0000-00000000d002",
    "RefreshOrdersDaily.DataPipeline": "00000000-0000-0000-0000-00000000d003",
    "OrdersAnalytics.SemanticModel": "00000000-0000-0000-0000-00000000d004",
}

# Mapping of directory suffix -> .platform metadata.type for Invariant 4.
EXPECTED_METADATA_TYPE: dict[str, str] = {
    "Sales.Lakehouse": "Lakehouse",
    "LoadOrders.Notebook": "Notebook",
    "RefreshOrdersDaily.DataPipeline": "DataPipeline",
    "OrdersAnalytics.SemanticModel": "SemanticModel",
}

# ENV-var refs the demo parameters.yml expects to be set at validate time.
# All set to dummy GUIDs in --dry-run mode so load_and_validate's $ENV
# resolution succeeds without operator credentials.
_DEMO_DRY_RUN_ENV_VARS: tuple[str, ...] = (
    "SIGANTRY_DEMO_WORKSPACE_ID",
    "SIGANTRY_DEMO_CAPACITY_ID",
    "SIGANTRY_FABRIC_WORKSPACE_ID_PREPROD",
    "SIGANTRY_FABRIC_WORKSPACE_ID_PROD",
    "SIGANTRY_FABRIC_CAPACITY_ID_PREPROD",
    "SIGANTRY_FABRIC_CAPACITY_ID_PROD",
)


# ---------------------------------------------------------------------------
# Parity helpers (Invariants 1 + 2: inherited from starter)
# ---------------------------------------------------------------------------


def _demo_dir(repo_root: Path) -> Path:
    """Resolve the demo dir under ``repo_root``."""
    return repo_root / DEMO_DIR_RELATIVE


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


def assert_pr_template_parity(demo_dir: Path) -> None:
    """Assert byte-identical fenced block across the PR-template trio.

    Raises :class:`RuntimeError` with a unified diff on divergence so
    the operator can see which side drifted.
    """
    gh_section = extract_pr_checklist_section(demo_dir / GITHUB_PR_TEMPLATE)
    ado_section = extract_pr_checklist_section(demo_dir / ADO_PR_TEMPLATE)
    partial = (demo_dir / PR_CHECKLIST_PARTIAL).read_text(encoding="utf-8").strip()

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


def assert_workflow_pair_parity(demo_dir: Path) -> None:
    """Assert both demo workflow YAMLs reference the required paths-filter.

    Raises :class:`RuntimeError` with the offending patterns on failure.
    """
    gha_path = demo_dir / GITHUB_WORKFLOW
    ado_path = demo_dir / ADO_WORKFLOW
    if not gha_path.is_file():
        raise RuntimeError(f"missing demo GHA workflow: {gha_path}")
    if not ado_path.is_file():
        raise RuntimeError(f"missing demo ADO job template: {ado_path}")

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

    ado_text = ado_path.read_text(encoding="utf-8")
    missing_ado = [p for p in REQUIRED_WORKFLOW_PATHS_FILTER if p not in ado_text]
    if missing_ado:
        raise RuntimeError(
            f"ADO job template missing required path pattern(s) {missing_ado!r} "
            f"(expected as YAML comment or trigger-config block in {ado_path})"
        )


# ---------------------------------------------------------------------------
# Demo-specific parity helpers (Invariants 3 + 4)
# ---------------------------------------------------------------------------


def assert_parameters_yml_validates(demo_dir: Path) -> None:
    """Invariant 3: templates/demo/parameters.yml validates clean.

    Imports ``sigantry_core.deploy.parameters.load_and_validate`` and
    runs it against ``templates/demo/parameters.yml``. The validator
    requires every ``$ENV:<VAR>`` reference to resolve to a SET
    environment variable, so we set the 6 demo + starter env vars to
    dummy GUIDs at the start of this function (only if not already
    set). This proves the file is structurally clean without
    requiring operator credentials.

    Raises :class:`RuntimeError` if the file is missing or fails
    validation.
    """
    params = demo_dir / DEMO_PARAMETERS_YML
    if not params.is_file():
        raise RuntimeError(f"missing demo parameters.yml: {params}")

    # Provide dummy GUIDs for any unset env var so $ENV resolution
    # succeeds in CI without operator creds. We do NOT clobber set
    # values (an operator running locally with real creds is fine).
    dummy = "00000000-0000-0000-0000-000000000001"
    for var in _DEMO_DRY_RUN_ENV_VARS:
        os.environ.setdefault(var, dummy)

    # Local import keeps script-level imports lean + lets unit tests
    # exercise the script without a sigantry install at module-load time.
    from sigantry_core.deploy.parameters import load_and_validate

    result = load_and_validate(params)
    seen = sorted(result.environments_seen)
    expected = ["DEV", "PREPROD", "PROD"]
    if sorted(seen) != sorted(expected):
        raise RuntimeError(f"demo parameters.yml: expected environments {expected}; got {seen}")


def assert_fabric_items_locked_logical_ids(demo_dir: Path) -> None:
    """Invariant 4: each fabric_items/<*>/.platform carries the locked logicalId.

    Walks the 4 expected directories (per ``EXPECTED_LOGICAL_IDS``); for
    each, parses the ``.platform`` JSON, asserts the ``config.logicalId``
    matches the locked d00X placeholder, and asserts ``metadata.type``
    matches the directory suffix.

    Raises :class:`RuntimeError` on the first mismatch.
    """
    items_root = demo_dir / DEMO_FABRIC_ITEMS_DIRNAME
    if not items_root.is_dir():
        raise RuntimeError(f"missing fabric_items dir: {items_root}")

    for item_dir, expected_lid in EXPECTED_LOGICAL_IDS.items():
        platform_path = items_root / item_dir / ".platform"
        if not platform_path.is_file():
            raise RuntimeError(f"missing .platform file: {platform_path}")
        try:
            data = json.loads(platform_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"{platform_path}: invalid JSON ({exc})") from exc

        config = data.get("config")
        if not isinstance(config, dict):
            raise RuntimeError(f"{platform_path}: missing or non-dict 'config' block")
        actual_lid = config.get("logicalId")
        if actual_lid != expected_lid:
            raise RuntimeError(
                f"{platform_path}: expected logicalId {expected_lid!r}; got {actual_lid!r}"
            )

        metadata = data.get("metadata")
        if not isinstance(metadata, dict):
            raise RuntimeError(f"{platform_path}: missing or non-dict 'metadata' block")
        actual_type = metadata.get("type")
        expected_type = EXPECTED_METADATA_TYPE[item_dir]
        if actual_type != expected_type:
            raise RuntimeError(
                f"{platform_path}: expected metadata.type {expected_type!r}; got {actual_type!r}"
            )


# ---------------------------------------------------------------------------
# Top-level commands
# ---------------------------------------------------------------------------


def export_dry_run(repo_root: Path) -> int:
    """Walk ``templates/demo/``; assert the 4 parity invariants; return exit code.

    Returns 0 on clean state. On parity failure prints a clear stderr
    message and returns a non-zero exit code (1 = parity drift, 2 =
    structural error such as a missing file).
    """
    demo = _demo_dir(repo_root)
    if not demo.is_dir():
        print(
            f"error: demo directory not found at {demo}",
            file=sys.stderr,
        )
        return 2

    try:
        assert_pr_template_parity(demo)
    except RuntimeError as exc:
        print(f"error: PR-template parity check failed:\n{exc}", file=sys.stderr)
        return 1

    try:
        assert_workflow_pair_parity(demo)
    except RuntimeError as exc:
        print(f"error: workflow-pair parity check failed:\n{exc}", file=sys.stderr)
        return 1

    try:
        assert_parameters_yml_validates(demo)
    except (RuntimeError, ValueError) as exc:
        print(f"error: parameters.yml validation failed:\n{exc}", file=sys.stderr)
        return 1

    try:
        assert_fabric_items_locked_logical_ids(demo)
    except RuntimeError as exc:
        print(f"error: fabric_items invariants failed:\n{exc}", file=sys.stderr)
        return 1

    # Stable, deterministic stdout for idempotency tests.
    print("export-demo: dry-run OK")
    print(f"  demo root:          {demo}")
    print("  pr-template parity: OK")
    print("  workflow parity:    OK")
    print("  parameters.yml:     OK")
    print("  fabric_items:       OK")
    return 0


def export_live_to_github(demo_dir: Path, target: str) -> None:
    """Live mirror to a sibling GitHub repo (operator-only).

    Per CONTEXT.md D-01 the live mirror is forbidden inside CI -- the
    operator runs the mirror procedure documented in
    ``.planning/phases/15-public-demo-environment/15-HUMAN-UAT.md``
    Test 1.
    """
    raise NotImplementedError(
        "Live mirror is operator-only per CONTEXT D-01. "
        "See .planning/phases/15-public-demo-environment/15-HUMAN-UAT.md "
        f"Test 1 for the documented procedure (target was {target!r}). "
        f"demo_dir={demo_dir}"
    )


def export_live_to_ado(demo_dir: Path, target_org: str, target_project: str | None) -> None:
    """Live mirror to an ADO project (operator-only)."""
    raise NotImplementedError(
        "Live mirror is operator-only per CONTEXT D-01. "
        "See .planning/phases/15-public-demo-environment/15-HUMAN-UAT.md "
        f"Test 1 for the documented procedure (target_org={target_org!r}, "
        f"target_project={target_project!r}). demo_dir={demo_dir}"
    )


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Sigantry demo-repo export tool. --dry-run is the default "
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
        help="Walk templates/demo/ and assert parity invariants. Default mode in CI.",
    )
    parser.add_argument(
        "--target-github",
        type=str,
        default=None,
        help=(
            "Target GitHub repo (e.g. 'sigantry/demo-sigantry') for the live "
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
    demo = _demo_dir(repo_root)

    # Live-mode flags trip NotImplementedError BEFORE the dry-run path so
    # an operator who mistakenly leaves --target-github set in CI gets a
    # non-zero exit + a clear pointer to 15-HUMAN-UAT.md.
    if args.target_github is not None:
        try:
            export_live_to_github(demo, args.target_github)
        except NotImplementedError as exc:
            print(f"error (operator-only): {exc}", file=sys.stderr)
            return 3
        return 0  # pragma: no cover -- unreachable; the call always raises.

    if args.target_ado_org is not None:
        try:
            export_live_to_ado(demo, args.target_ado_org, args.target_ado_project)
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
