#!/usr/bin/env python3
"""sigantry: tag-pin lint over every templates/**/*.yml + azure-pipelines.yml (Pitfall A).

Every `resources.repositories` entry whose `repository` alias is NOT `self`
MUST pin `ref: refs/tags/<tag>`. Branch refs (`refs/heads/...`) and missing
`ref:` keys are rejected - the latter is the ADO default which silently
resolves to `refs/heads/main` on the target repo (Pitfall A footgun).

Sources (Pitfall 14 / Phase 5 Success Criterion 1):
  Microsoft Learn - Repository resources (ref semantics)
    https://learn.microsoft.com/azure/devops/pipelines/process/resources
    ms.date: 2026-04-20
  Microsoft Learn - Use templates to enforce pipeline restrictions
    https://learn.microsoft.com/azure/devops/pipelines/security/templates
    ms.date: 2026-04-20

Usage:
  python scripts/ci/check-template-refs.py         # exits 0 if clean, 1 otherwise
  python scripts/ci/check-template-refs.py --help  # print this docstring

(Renamed from the fabric-dataops-toolkits tag-pin lint in Plan 10-02 per ADR-0011.)

Output format (ADO log parser compatible):
  ##[error] <file>: repository '<alias>' has ref='<ref>' - must start with refs/tags/
  OK - audited N YAML file(s), no tag-pin violations
"""

from __future__ import annotations

import re
import sys
from collections.abc import Iterable
from pathlib import Path
from typing import Any

try:
    import yaml  # PyYAML

    _HAS_PYYAML = True
except ImportError:  # pragma: no cover - defensive fallback
    _HAS_PYYAML = False


# Regex fallback for when PyYAML is unavailable (Rule 2 deviation guard).
_REPO_BLOCK = re.compile(
    r"""
    ^\s*-\s*repository:\s*['"]?(?P<alias>[^'"\s]+)['"]?\s*$
    (?:(?:^\s*\w[\w-]*:.*$\n?)*?)                # any intervening keys
    (?:^\s*ref:\s*(?P<ref>\S+)\s*$)?             # optional ref line
    """,
    re.MULTILINE | re.VERBOSE,
)


def _format_violation(path: Path, alias: str, ref: str | None) -> str:
    shown = ref if ref else "<missing>"
    return f"{path}: repository '{alias}' has ref='{shown}' - must start with refs/tags/"


def _iter_repo_entries(doc: Any) -> Iterable[dict]:
    if not isinstance(doc, dict):
        return
    resources = doc.get("resources")
    if not isinstance(resources, dict):
        return
    repos = resources.get("repositories")
    if not isinstance(repos, list):
        return
    for entry in repos:
        if isinstance(entry, dict):
            yield entry


def audit(yaml_path: Path) -> list[str]:
    """Return a list of violation messages (empty = clean)."""
    try:
        text = yaml_path.read_text(encoding="utf-8")
    except OSError as e:
        return [f"{yaml_path}: read error: {e}"]

    if _HAS_PYYAML:
        try:
            doc = yaml.safe_load(text)
        except yaml.YAMLError as e:
            return [f"{yaml_path}: YAML parse error: {e}"]

        violations: list[str] = []
        for entry in _iter_repo_entries(doc):
            alias = entry.get("repository")
            if alias is None or alias == "self":
                continue
            ref = entry.get("ref")
            if ref is None or not str(ref).startswith("refs/tags/"):
                violations.append(_format_violation(yaml_path, str(alias), ref))
        return violations

    # Regex fallback path (Rule 2 deviation).
    violations = []
    for m in _REPO_BLOCK.finditer(text):
        alias = m.group("alias")
        ref = m.group("ref")
        if alias == "self":
            continue
        if ref is None or not ref.startswith("refs/tags/"):
            violations.append(_format_violation(yaml_path, alias, ref))
    return violations


def _find_targets(root: Path) -> list[Path]:
    targets: list[Path] = []
    templates_dir = root / "templates"
    if templates_dir.is_dir():
        targets.extend(sorted(templates_dir.rglob("*.yml")))
    azure_pipelines = root / "azure-pipelines.yml"
    if azure_pipelines.is_file():
        targets.append(azure_pipelines)
    return targets


def main(argv: list[str] | None = None) -> int:
    args = list(argv) if argv is not None else []
    if any(a in ("-h", "--help") for a in args):
        print(__doc__ or "")
        return 0
    root = Path(".")
    targets = _find_targets(root)

    if not targets:
        print(f"OK - audited 0 YAML file(s) under {root.resolve()}, no tag-pin violations")
        return 0

    all_violations: list[str] = []
    for p in targets:
        for v in audit(p):
            all_violations.append(v)

    if all_violations:
        for msg in all_violations:
            print(f"##[error] {msg}", file=sys.stderr)
        print(
            f"{len(all_violations)} tag-pin violation(s) across {len(targets)} file(s)",
            file=sys.stderr,
        )
        return 1

    print(f"OK - audited {len(targets)} YAML file(s), no tag-pin violations")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
