"""Starter-suite tests for ``scripts/export-starter.py`` (Plan 14-07 Task 1 / STARTER-01).

Replaces the three Wave 0 ``pytest.xfail`` stubs with real assertions
that invoke ``scripts/export-starter.py --dry-run`` via subprocess. The
deeper isolation tests (synthetic divergence + repeated runs) live at
``tests/scripts/test_export_starter_idempotent.py``; this file is the
``tests/starter/`` smoke-test entry point that the per-plan validation
map cites for STARTER-01 closure.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "scripts" / "export-starter.py"
COMMITTED_STARTER = REPO_ROOT / "templates" / "starter"


def _run_export(args: list[str], cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        cwd=str(cwd or REPO_ROOT),
        capture_output=True,
        text=True,
        check=False,
    )


def test_export_dry_run_clean_on_committed_starter() -> None:
    """``--dry-run`` exits 0 with zero diffs against the committed templates/starter/ (D-03)."""
    result = _run_export(["--dry-run"])
    assert result.returncode == 0, (
        f"export --dry-run exited {result.returncode!r}; "
        f"stdout={result.stdout!r} stderr={result.stderr!r}"
    )


def test_export_dry_run_fails_when_pr_templates_diverge(tmp_path: Path) -> None:
    """Divergence between ``.github/pull_request_template.md`` and ``_partials/pr-checklist.md`` -> non-zero exit."""
    fake_repo = tmp_path / "fake_repo"
    fake_starter = fake_repo / "templates" / "starter"
    shutil.copytree(COMMITTED_STARTER, fake_starter)

    ado_pr = fake_starter / ".azuredevops" / "pull_request_template.md"
    text = ado_pr.read_text(encoding="utf-8")
    mutated = text.replace(
        "**Variable-library changes**",
        "**Variable-library changes (DIVERGED)**",
    )
    assert mutated != text, "test setup error: marker substring missing"
    ado_pr.write_text(mutated, encoding="utf-8")

    result = _run_export(["--dry-run", "--root", str(fake_repo)])
    assert result.returncode != 0, (
        "expected non-zero exit when PR templates diverge; "
        f"stdout={result.stdout!r} stderr={result.stderr!r}"
    )


def test_export_idempotent_second_run() -> None:
    """Two consecutive ``--dry-run`` invocations both exit 0 with identical output."""
    first = _run_export(["--dry-run"])
    second = _run_export(["--dry-run"])
    assert first.returncode == 0
    assert second.returncode == 0
    assert first.stdout == second.stdout
