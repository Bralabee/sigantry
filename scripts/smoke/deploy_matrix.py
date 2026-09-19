"""Smoke-deploy matrix for Success Criterion 5.

Usage:
    python -m scripts.smoke.deploy_matrix \\
        --workspace-id <WS> --source <DIR> --junit-xml smoke.xml

Invoked on every ``fabric-cicd`` version bump. Gated by
``PYTEST_RUN_SMOKE=1`` in CI; emits a per-item-type JUnit XML that Phase 5
pipeline templates consume.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

_CANONICAL_TYPES: tuple[str, ...] = (
    "Lakehouse",
    "Environment",
    "Notebook",
    "DataPipeline",
)


def run_matrix(
    *,
    workspace_id: str,
    repository_directory: str,
    environment: str = "SMOKE",
    item_types: tuple[str, ...] = _CANONICAL_TYPES,
) -> dict[str, str]:
    """Deploy each item type in isolation; return per-type status map.

    Returns:
        dict mapping item-type name to ``"Succeeded"`` or a ``"Failed: ..."``
        string. Failures are captured — NOT re-raised — so the matrix can
        report every type rather than short-circuiting on the first failure.
    """
    from sigantry_core.deploy.core import deploy_workspace

    results: dict[str, str] = {}
    for item_type in item_types:
        try:
            deploy_workspace(
                workspace_id=workspace_id,
                repository_directory=repository_directory,
                environment=environment,
                item_type_in_scope=[item_type],
            )
            results[item_type] = "Succeeded"
        except Exception as exc:  # smoke reports failures; does NOT re-raise
            results[item_type] = f"Failed: {type(exc).__name__}: {exc}"
    return results


def emit_junit_xml(results: dict[str, str], out_path: Path) -> None:
    """Minimal JUnit XML writer — one testsuite, one testcase per item type."""
    failures = sum(1 for v in results.values() if v != "Succeeded")
    tests = len(results)
    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        f'<testsuite name="fabric-cicd-smoke" tests="{tests}" failures="{failures}">',
    ]
    for item_type, status in results.items():
        if status == "Succeeded":
            lines.append(f'  <testcase classname="smoke" name="{item_type}"/>')
        else:
            msg = status.replace('"', "&quot;").replace("<", "&lt;").replace(">", "&gt;")
            lines.append(
                f'  <testcase classname="smoke" name="{item_type}">'
                f'<failure message="{msg}"/></testcase>'
            )
    lines.append("</testsuite>")
    out_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="fabric-cicd-smoke", description="fabric-cicd smoke matrix"
    )
    parser.add_argument("--workspace-id", required=True)
    parser.add_argument(
        "--source",
        required=True,
        help="Repository directory with a minimal item tree.",
    )
    parser.add_argument("--environment", default="SMOKE")
    parser.add_argument("--junit-xml", default="smoke.xml")
    args = parser.parse_args(argv)

    results = run_matrix(
        workspace_id=args.workspace_id,
        repository_directory=args.source,
        environment=args.environment,
    )
    emit_junit_xml(results, Path(args.junit_xml))
    for item_type, status in results.items():
        print(f"{item_type}: {status}")
    return 0 if all(v == "Succeeded" for v in results.values()) else 1


if __name__ == "__main__":
    sys.exit(main())
