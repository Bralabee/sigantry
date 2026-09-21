#!/usr/bin/env python3
"""sigantry: dual-CI parity lint (BRIEF-06 / Phase 10 Plan 06).

Enforces the parity rule from ``docs/reference/dual-ci-strategy.md``:
every user-facing feature producing a CI template / workflow / scheduled
runner ships in BOTH Azure DevOps (``templates/{stages,schedules,pr-review}/<name>.yml``)
AND GitHub Actions (``.github/workflows/<name>.yml``) variants with the
SAME basename, the SAME stage graph, and the SAME parameter names + types.
A merge that updates only one side is blocked by this lint.

The lint runs three checks:

1. **Pair existence.** For every ADO template under
   ``templates/{stages,schedules,pr-review}/``, assert there is a GHA
   workflow under ``.github/workflows/`` with the same basename, and
   vice versa. Missing counterpart -> red.
2. **Stage-graph parity.** For each pair, parse both YAMLs and assert
   the stage names + dependency edges are identical. Step bodies may
   differ; the graph may not.
3. **Parameter parity.** For each pair, assert input parameters
   declared on both sides have the same names + (normalised) types.
   Extra parameters require a matching counterpart or an explicit
   ``sigantry-dual-ci-ignore: <reason>`` annotation in both files.

Two narrow, header-annotated exceptions skip the entire pair-existence +
graph + parameter checks for the affected file:

- ``sigantry-dual-ci-exception: ci-mechanics`` -- CI-system-specific
  tooling (e.g. ``publish-ado-wiki.yml`` vs ``publish-gh-pages.yml``).
- ``sigantry-dual-ci-exception: bootstrap`` -- one-shot bootstrap
  scripts (e.g. service-connection-bootstrap vs
  workflow-identity-bootstrap).

Files under ``templates/extends/`` are meta-templates (composition
helpers, not user-facing features) and are skipped from pair discovery
by design.

Adding a third exception category requires an ADR per
``docs/reference/dual-ci-strategy.md``.

Threat mitigations:
    T-10-06-01 (Tampering / YAML deserialisation): only ``yaml.safe_load``
    is used. ``yaml.load`` with a Loader is forbidden anywhere in this
    file -- the gate's acceptance test greps for the unsafe call.

Usage:
    python scripts/ci/check-dual-ci-parity.py             # repo root
    python scripts/ci/check-dual-ci-parity.py --root <p>  # custom root
    python scripts/ci/check-dual-ci-parity.py --verbose   # per-pair logs

Exit codes:
    0 -- clean (all pairs match; exceptions skipped).
    1 -- at least one parity violation; CI should fail.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from typing import Any

import yaml  # PyYAML; safe_load only (T-10-06-01).

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

ADO_SUBDIRS: tuple[str, ...] = ("stages", "schedules", "pr-review")
"""Allowed ADO template subdirectories under ``templates/``."""

GHA_WORKFLOW_DIR = Path(".github") / "workflows"
"""Relative path to the GitHub Actions workflows directory."""

ADO_TEMPLATES_DIR = Path("templates")
"""Relative path to the ADO templates root."""

EXTENDS_DIR = "extends"
"""Sub-directory under ``templates/`` whose files are meta-templates and
are skipped from pair discovery by design (composition helpers)."""

# Match either documented exception category, anchored to the literal
# annotation token. The pattern is intentionally narrow: only
# ``ci-mechanics`` and ``bootstrap`` are valid; any other value is a
# silent miss and the file is treated as a normal pair candidate.
EXCEPTION_PATTERN = re.compile(r"sigantry-dual-ci-exception:\s*(ci-mechanics|bootstrap)\b")

# Per-parameter ignore annotation, used to silence parameter-parity
# violations on a specific extra parameter (must appear in BOTH files
# with the same reason).
IGNORE_PATTERN = re.compile(r"sigantry-dual-ci-ignore:\s*(.+)")

# How many lines of file header to scan for the exception annotation.
# The strategy doc says "annotated in the header" -- 20 lines is
# generous for a file with a copyright + descriptive comment block.
EXCEPTION_HEADER_LINES = 20

# Type-name normalisation across ADO YAML and GHA workflow_call inputs.
_TYPE_NORMALISATION = {
    "boolean": "boolean",
    "bool": "boolean",
    "string": "string",
    "str": "string",
    "number": "number",
    "integer": "number",
    "int": "number",
    "object": "object",
    "step": "step",
    "stepList": "stepList",
    "job": "job",
    "jobList": "jobList",
    "stage": "stage",
    "stageList": "stageList",
}


# ---------------------------------------------------------------------------
# Header / annotation helpers
# ---------------------------------------------------------------------------


def _read_header_lines(file: Path, limit: int = EXCEPTION_HEADER_LINES) -> list[str]:
    """Return the first ``limit`` lines of ``file`` (decoded UTF-8).

    Errors are tolerated: missing files / decode failures produce an
    empty list so the caller treats the file as un-annotated.
    """
    try:
        with file.open("r", encoding="utf-8", errors="replace") as fh:
            return [next(fh, "") for _ in range(limit)]
    except OSError:
        return []


def has_exception_annotation(file: Path) -> str | None:
    """Return the exception category if the file is annotated; else ``None``.

    Categories: ``ci-mechanics``, ``bootstrap``. Any other value does
    not match (deliberately narrow per dual-ci-strategy.md).
    """
    for line in _read_header_lines(file):
        match = EXCEPTION_PATTERN.search(line)
        if match:
            return match.group(1)
    return None


def file_ignores(file: Path) -> set[str]:
    """Return the set of ``sigantry-dual-ci-ignore`` reasons declared in the
    header of ``file``. Used to silence specific parameter-parity errors.
    """
    reasons: set[str] = set()
    for line in _read_header_lines(file):
        match = IGNORE_PATTERN.search(line)
        if match:
            reasons.add(match.group(1).strip())
    return reasons


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------


def find_ado_templates(root: Path) -> list[Path]:
    """Return ADO templates under ``root/templates/{stages,schedules,pr-review}/``.

    Excludes anything under ``templates/extends/`` (meta-templates) and
    files carrying a documented ``sigantry-dual-ci-exception`` header.
    """
    out: list[Path] = []
    base = root / ADO_TEMPLATES_DIR
    if not base.is_dir():
        return out
    for sub in ADO_SUBDIRS:
        sub_dir = base / sub
        if not sub_dir.is_dir():
            continue
        for path in sorted(sub_dir.glob("*.yml")):
            if not path.is_file():
                continue
            # Skip meta-templates (defensive; extends/ is not in ADO_SUBDIRS,
            # but a future contributor might rename a subdir).
            if EXTENDS_DIR in path.parts:
                continue
            if has_exception_annotation(path):
                continue
            out.append(path)
    return out


def find_gha_workflows(root: Path) -> list[Path]:
    """Return GHA workflows under ``root/.github/workflows/`` (excluding
    exception-annotated files).
    """
    out: list[Path] = []
    base = root / GHA_WORKFLOW_DIR
    if not base.is_dir():
        return out
    for path in sorted(base.glob("*.yml")):
        if not path.is_file():
            continue
        if has_exception_annotation(path):
            continue
        out.append(path)
    return out


def _count_exceptions(root: Path) -> int:
    """Return the number of exception-annotated YAML files under ``root``
    (used in the summary line)."""
    n = 0
    base_ado = root / ADO_TEMPLATES_DIR
    if base_ado.is_dir():
        for sub in ADO_SUBDIRS:
            sub_dir = base_ado / sub
            if not sub_dir.is_dir():
                continue
            for path in sub_dir.glob("*.yml"):
                if path.is_file() and has_exception_annotation(path):
                    n += 1
    base_gha = root / GHA_WORKFLOW_DIR
    if base_gha.is_dir():
        for path in base_gha.glob("*.yml"):
            if path.is_file() and has_exception_annotation(path):
                n += 1
    return n


# ---------------------------------------------------------------------------
# Pair existence
# ---------------------------------------------------------------------------


def check_pair_existence(ado_files: list[Path], gha_files: list[Path]) -> list[str]:
    """Return error messages for ADO templates without a GHA counterpart
    and vice versa, matched by basename.
    """
    errors: list[str] = []
    ado_basenames = {f.name: f for f in ado_files}
    gha_basenames = {f.name: f for f in gha_files}

    for name, ado_path in ado_basenames.items():
        if name not in gha_basenames:
            errors.append(
                f"{ado_path}: ADO template has no GHA counterpart at "
                f".github/workflows/{name} "
                f"(annotate as sigantry-dual-ci-exception or add the GHA pair)"
            )
    for name, gha_path in gha_basenames.items():
        if name not in ado_basenames:
            errors.append(
                f"{gha_path}: GHA workflow has no ADO counterpart at "
                f"templates/{{stages,schedules,pr-review}}/{name} "
                f"(annotate as sigantry-dual-ci-exception or add the ADO pair)"
            )
    return errors


# ---------------------------------------------------------------------------
# Stage-graph parity
# ---------------------------------------------------------------------------


def _safe_load_yaml(file: Path) -> Any:
    """Parse YAML via ``yaml.safe_load`` (T-10-06-01 mitigation)."""
    return yaml.safe_load(file.read_text(encoding="utf-8"))


def _ado_stage_graph(doc: Any) -> dict[str, set[str]]:
    """Extract ``{stage_name: set(dependsOn names)}`` from an ADO doc.

    Sanitisation rules:
    - Missing ``stages:`` key -> empty graph.
    - ``dependsOn`` may be a string, list, or absent. Absent / empty
      list -> empty edge set. Single string -> singleton edge set.
    """
    graph: dict[str, set[str]] = {}
    if not isinstance(doc, dict):
        return graph
    stages = doc.get("stages")
    if not isinstance(stages, list):
        return graph
    for stage in stages:
        if not isinstance(stage, dict):
            continue
        name = stage.get("stage")
        if not isinstance(name, str):
            continue
        deps = stage.get("dependsOn")
        if deps is None:
            edge_set: set[str] = set()
        elif isinstance(deps, str):
            edge_set = {deps} if deps else set()
        elif isinstance(deps, list):
            edge_set = {d for d in deps if isinstance(d, str)}
        else:
            edge_set = set()
        graph[name] = edge_set
    return graph


def _gha_stage_graph(doc: Any) -> dict[str, set[str]]:
    """Extract ``{job_name: set(needs names)}`` from a GHA doc.

    GHA's ``jobs:`` map is the equivalent of ADO ``stages:``. ``needs``
    may be a string, list, or absent.
    """
    graph: dict[str, set[str]] = {}
    if not isinstance(doc, dict):
        return graph
    jobs = doc.get("jobs")
    if not isinstance(jobs, dict):
        return graph
    for name, body in jobs.items():
        if not isinstance(name, str):
            continue
        if not isinstance(body, dict):
            graph[name] = set()
            continue
        needs = body.get("needs")
        if needs is None:
            edge_set: set[str] = set()
        elif isinstance(needs, str):
            edge_set = {needs} if needs else set()
        elif isinstance(needs, list):
            edge_set = {d for d in needs if isinstance(d, str)}
        else:
            edge_set = set()
        graph[name] = edge_set
    return graph


def parse_stage_graph(file: Path, *, is_ado: bool) -> dict[str, set[str]]:
    """Parse the stage / job graph from ``file``.

    ADO: ``stages[*].stage`` + ``stages[*].dependsOn``.
    GHA: ``jobs.*`` (keys) + ``jobs[*].needs``.
    """
    doc = _safe_load_yaml(file)
    return _ado_stage_graph(doc) if is_ado else _gha_stage_graph(doc)


def check_stage_graph_parity(ado_file: Path, gha_file: Path) -> list[str]:
    """Return error messages if the ADO + GHA graphs differ on either
    node set or edge set.
    """
    errors: list[str] = []
    ado_graph = parse_stage_graph(ado_file, is_ado=True)
    gha_graph = parse_stage_graph(gha_file, is_ado=False)

    ado_nodes = set(ado_graph.keys())
    gha_nodes = set(gha_graph.keys())
    if ado_nodes != gha_nodes:
        only_ado = sorted(ado_nodes - gha_nodes)
        only_gha = sorted(gha_nodes - ado_nodes)
        errors.append(
            f"{ado_file.name}: stage-graph node mismatch -- "
            f"only-in-ADO={only_ado!r} only-in-GHA={only_gha!r}"
        )
        # Stop here for this pair: edge comparison on diverged node sets
        # produces noise; the operator must reconcile names first.
        return errors

    for node in sorted(ado_nodes):
        if ado_graph[node] != gha_graph[node]:
            errors.append(
                f"{ado_file.name}: stage-graph edge mismatch on node "
                f"'{node}' -- ADO dependsOn={sorted(ado_graph[node])!r} "
                f"GHA needs={sorted(gha_graph[node])!r}"
            )
    return errors


# ---------------------------------------------------------------------------
# Parameter parity
# ---------------------------------------------------------------------------


def _normalise_type(raw: Any) -> str:
    """Map an ADO/GHA type literal to the normalised form used for parity."""
    if not isinstance(raw, str):
        return ""
    return _TYPE_NORMALISATION.get(raw.strip(), raw.strip())


def _ado_parameters(doc: Any) -> dict[str, str]:
    """Extract ``{name: normalised_type}`` from an ADO ``parameters`` block."""
    out: dict[str, str] = {}
    if not isinstance(doc, dict):
        return out
    params = doc.get("parameters")
    if not isinstance(params, list):
        return out
    for entry in params:
        if not isinstance(entry, dict):
            continue
        name = entry.get("name")
        if not isinstance(name, str):
            continue
        out[name] = _normalise_type(entry.get("type"))
    return out


def _gha_on(doc: Any) -> Any:
    """Return the value of the GHA workflow's ``on:`` key.

    PyYAML 1.1 parses the literal ``on`` as the Python boolean ``True``
    (the YAML 1.1 truthy alias). Modern GHA workflows always write
    ``on:`` unquoted, so the parsed dict's key is ``True`` rather than
    ``"on"``. Look up both spellings to stay robust regardless of how
    the workflow file is authored.
    """
    if not isinstance(doc, dict):
        return None
    if "on" in doc:
        return doc["on"]
    if True in doc:
        return doc[True]
    return None


def _gha_parameters(doc: Any) -> dict[str, str]:
    """Extract ``{name: normalised_type}`` from a GHA ``on.workflow_call.inputs`` block."""
    out: dict[str, str] = {}
    if not isinstance(doc, dict):
        return out
    on = _gha_on(doc)
    inputs: Any = None
    if isinstance(on, dict):
        wc = on.get("workflow_call")
        if isinstance(wc, dict):
            inputs = wc.get("inputs")
    elif isinstance(on, str):
        # ``on: workflow_call`` shorthand without inputs -> no params.
        return out
    if not isinstance(inputs, dict):
        return out
    for name, body in inputs.items():
        if not isinstance(name, str):
            continue
        if isinstance(body, dict):
            out[name] = _normalise_type(body.get("type"))
        else:
            out[name] = ""
    return out


def extract_parameters(file: Path, *, is_ado: bool) -> dict[str, str]:
    """Return ``{name: normalised_type}`` for the file's parameter surface."""
    doc = _safe_load_yaml(file)
    return _ado_parameters(doc) if is_ado else _gha_parameters(doc)


def check_parameter_parity(ado_file: Path, gha_file: Path) -> list[str]:
    """Return error messages if ADO + GHA parameter surfaces differ.

    Honours per-file ``sigantry-dual-ci-ignore: <reason>`` annotations:
    if the same ignore reason appears in BOTH files' headers, an extra
    parameter on either side is allowed.
    """
    errors: list[str] = []
    ado_params = extract_parameters(ado_file, is_ado=True)
    gha_params = extract_parameters(gha_file, is_ado=False)
    shared_ignores = file_ignores(ado_file) & file_ignores(gha_file)

    only_ado = sorted(set(ado_params) - set(gha_params))
    only_gha = sorted(set(gha_params) - set(ado_params))

    if only_ado and not shared_ignores:
        errors.append(
            f"{ado_file.name}: parameters present only on ADO side: "
            f"{only_ado!r} (add to GHA workflow_call.inputs or annotate "
            f"both files with sigantry-dual-ci-ignore: <reason>)"
        )
    if only_gha and not shared_ignores:
        errors.append(
            f"{ado_file.name}: parameters present only on GHA side: "
            f"{only_gha!r} (add to ADO parameters: or annotate both "
            f"files with sigantry-dual-ci-ignore: <reason>)"
        )

    for name in sorted(set(ado_params) & set(gha_params)):
        if ado_params[name] != gha_params[name]:
            errors.append(
                f"{ado_file.name}: parameter '{name}' type mismatch -- "
                f"ADO={ado_params[name]!r} GHA={gha_params[name]!r}"
            )
    return errors


# ---------------------------------------------------------------------------
# Driver
# ---------------------------------------------------------------------------


def _run_checks(root: Path, *, verbose: bool) -> tuple[int, int, list[str]]:
    """Discover pairs under ``root``, run all three checks, return
    (pairs_checked, exceptions_skipped, errors).
    """
    ado_files = find_ado_templates(root)
    gha_files = find_gha_workflows(root)
    exceptions = _count_exceptions(root)

    errors = check_pair_existence(ado_files, gha_files)

    # For pairs that exist on both sides, run the deeper checks.
    ado_by_name = {f.name: f for f in ado_files}
    gha_by_name = {f.name: f for f in gha_files}
    pair_names = sorted(set(ado_by_name) & set(gha_by_name))

    for name in pair_names:
        ado_path = ado_by_name[name]
        gha_path = gha_by_name[name]
        pair_errors: list[str] = []
        pair_errors.extend(check_stage_graph_parity(ado_path, gha_path))
        pair_errors.extend(check_parameter_parity(ado_path, gha_path))
        if verbose:
            status = "OK" if not pair_errors else "FAIL"
            print(f"[{status}] pair '{name}': ADO={ado_path} GHA={gha_path}")
        errors.extend(pair_errors)

    return len(pair_names), exceptions, errors


def main(argv: list[str] | None = None) -> int:
    """CLI entry point. Return 0 on clean, 1 on any parity violation."""
    parser = argparse.ArgumentParser(
        prog="check-dual-ci-parity",
        description=(
            "dual-ci-parity lint -- enforces BRIEF-06 parity rule from "
            "docs/reference/dual-ci-strategy.md."
        ),
    )
    parser.add_argument(
        "--root",
        default=".",
        help="Repository root to scan (default: current directory).",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Print one line per pair with PASS/FAIL status.",
    )
    args = parser.parse_args(argv)

    root = Path(args.root).resolve()
    if not root.is_dir():
        print(f"check-dual-ci-parity: --root '{root}' is not a directory")
        return 1

    pairs, exceptions, errors = _run_checks(root, verbose=args.verbose)

    if errors:
        for msg in errors:
            print(f"##[error] {msg}")
        print(
            f"check-dual-ci-parity: pairs={pairs} exceptions={exceptions} "
            f"errors={len(errors)} -- FAIL"
        )
        return 1

    print(f"check-dual-ci-parity: pairs={pairs} exceptions={exceptions} errors=0 -- OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
