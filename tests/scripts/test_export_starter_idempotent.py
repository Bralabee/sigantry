"""Idempotency tests for ``scripts/export-starter.py`` (Plan 14-07 Task 1 / STARTER-01).

The script ships in **dry-run-only** mode in this monorepo (see
``scripts/export-starter.py`` module docstring + CONTEXT.md D-01..D-03):

- ``--dry-run`` (default, CI-safe) walks ``templates/starter/`` and asserts
  parity invariants between the two PR templates and the two CI workflow
  YAMLs. Exits 0 on clean state, non-zero on divergence.
- ``--target-github`` / ``--target-ado-org`` raise ``NotImplementedError``;
  the live mirror is operator-only per CONTEXT D-01 (creating sibling
  GitHub repos / ADO project templates from inside CI is forbidden).

These tests exercise only the dry-run code path, which is the load-bearing
guarantee for STARTER-01 closure (the export script's CI-side gate).
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
    """Invoke ``scripts/export-starter.py`` via the same Python interpreter.

    Returns the completed process; never raises on non-zero exit (callers
    assert exit codes themselves).
    """
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        cwd=str(cwd or REPO_ROOT),
        capture_output=True,
        text=True,
        check=False,
    )


def test_export_dry_run_clean() -> None:
    """``python scripts/export-starter.py --dry-run`` exits 0 against the committed tree (D-03)."""
    result = _run_export(["--dry-run"])
    assert result.returncode == 0, (
        f"export --dry-run exited {result.returncode!r}; "
        f"stdout={result.stdout!r} stderr={result.stderr!r}"
    )


def test_export_dry_run_fails_on_pr_template_divergence(tmp_path: Path) -> None:
    """If ``.github/pull_request_template.md`` diverges from ``_partials/pr-checklist.md``, dry-run exits non-zero."""
    # Stage a copy of the committed starter under tmp_path/<copy>/templates/starter/
    # so the script's REPO_ROOT relative-path discipline still works.
    fake_repo = tmp_path / "fake_repo"
    fake_starter = fake_repo / "templates" / "starter"
    shutil.copytree(COMMITTED_STARTER, fake_starter)
    # Mutate ONLY the GitHub PR template's fenced region: flip a checkbox
    # that lives inside the ``<!-- pr-checklist:start -->`` ... ``end -->``
    # block. The ADO template + the partial stay untouched, so the export
    # script's parity assertion must trip.
    gh_pr = fake_starter / ".github" / "pull_request_template.md"
    text = gh_pr.read_text(encoding="utf-8")
    mutated = text.replace(
        "**Test evidence**",
        "**Test evidence (DIVERGED FROM PARTIAL)**",
    )
    assert mutated != text, "test setup error: marker substring not found in PR template"
    gh_pr.write_text(mutated, encoding="utf-8")

    result = _run_export(["--dry-run", "--root", str(fake_repo)])
    assert result.returncode != 0, (
        f"export --dry-run should fail on divergent PR templates but exited 0; "
        f"stdout={result.stdout!r} stderr={result.stderr!r}"
    )
    combined = (result.stdout + result.stderr).lower()
    assert (
        "diverg" in combined or "differ" in combined or "drift" in combined or "parity" in combined
    ), (
        "expected diff/divergence/drift/parity message in stderr; "
        f"got stdout={result.stdout!r} stderr={result.stderr!r}"
    )


def test_export_idempotent_second_run() -> None:
    """Running ``--dry-run`` twice in a row is a clean no-op both times."""
    first = _run_export(["--dry-run"])
    assert first.returncode == 0, f"first --dry-run failed: stderr={first.stderr!r}"
    second = _run_export(["--dry-run"])
    assert second.returncode == 0, f"second --dry-run failed: stderr={second.stderr!r}"
    # Best-effort byte-equal output assertion: the script is deterministic.
    assert first.stdout == second.stdout, (
        "export --dry-run output diverged across two consecutive runs (non-idempotent)"
    )


def test_export_target_github_raises_not_implemented() -> None:
    """Live-mode is operator-only per CONTEXT D-01; the flag must raise NotImplementedError."""
    result = _run_export(["--target-github", "sigantry/sigantry-starter"])
    assert result.returncode != 0, (
        "live --target-github must NOT silently succeed inside CI; "
        f"stdout={result.stdout!r} stderr={result.stderr!r}"
    )
    combined = (result.stdout + result.stderr).lower()
    assert "operator" in combined or "notimplemented" in combined or "human-uat" in combined, (
        "expected an operator-only / NotImplementedError signal in output; "
        f"got stdout={result.stdout!r} stderr={result.stderr!r}"
    )


def test_export_target_ado_raises_not_implemented() -> None:
    """Live-mode for ADO is operator-only per CONTEXT D-01."""
    result = _run_export(["--target-ado-org", "sigantry-test-org"])
    assert result.returncode != 0, (
        "live --target-ado-org must NOT silently succeed inside CI; "
        f"stdout={result.stdout!r} stderr={result.stderr!r}"
    )
    combined = (result.stdout + result.stderr).lower()
    assert "operator" in combined or "notimplemented" in combined or "human-uat" in combined, (
        "expected an operator-only / NotImplementedError signal in output; "
        f"got stdout={result.stdout!r} stderr={result.stderr!r}"
    )
