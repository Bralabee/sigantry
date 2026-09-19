"""Plan 15-01 -- scripts/export-demo.py idempotency invariants.

Plan 15-00 stamped three xfail stubs; Plan 15-01 ships
scripts/export-demo.py (byte-extension of scripts/export-starter.py)
and flips these xfails. The script's CI-side guarantee is PARITY,
not propagation: --dry-run walks templates/demo/ and asserts the 4
parity invariants (PR-template fenced block, workflow paths-filter,
parameters.yml validates, fabric_items locked logicalIds).
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]
_SCRIPT = _REPO / "scripts" / "export-demo.py"


def _run_export(args: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(_SCRIPT), *args],
        cwd=str(_REPO),
        capture_output=True,
        text=True,
        check=False,
    )


def test_export_demo_dry_run_clean() -> None:
    """`scripts/export-demo.py --dry-run` exits 0 and reports zero pending file changes (Plan 15-01)."""
    result = _run_export(["--dry-run"])
    assert result.returncode == 0, (
        f"export-demo --dry-run exited {result.returncode!r}; "
        f"stdout={result.stdout!r} stderr={result.stderr!r}"
    )
    assert "export-demo: dry-run OK" in result.stdout
    assert "pr-template parity: OK" in result.stdout
    assert "workflow parity:    OK" in result.stdout
    assert "parameters.yml:     OK" in result.stdout
    assert "fabric_items:       OK" in result.stdout


def test_export_demo_idempotent_second_run() -> None:
    """Two consecutive `scripts/export-demo.py --dry-run` invocations both exit 0 with identical stdout."""
    first = _run_export(["--dry-run"])
    second = _run_export(["--dry-run"])
    assert first.returncode == 0
    assert second.returncode == 0
    assert first.stdout == second.stdout, (
        f"export-demo dry-run is non-deterministic:\n"
        f"first={first.stdout!r}\nsecond={second.stdout!r}"
    )


def test_export_demo_live_flag_raises_not_implemented() -> None:
    """`scripts/export-demo.py --target-github` is operator-bound and refuses to run from CI (Plan 15-01)."""
    result = _run_export(["--target-github", "sigantry/demo-sigantry"])
    assert result.returncode != 0, (
        f"--target-github should exit non-zero; got {result.returncode}; "
        f"stdout={result.stdout!r} stderr={result.stderr!r}"
    )
    combined = (result.stderr + result.stdout).lower()
    assert "operator-only" in combined or "notimplementederror" in combined, (
        f"expected operator-only / NotImplementedError in stderr; got {result.stderr!r}"
    )

    # Same expectation for --target-ado-org.
    ado_result = _run_export(
        ["--target-ado-org", "sigantry-test", "--target-ado-project", "demo-sigantry"]
    )
    assert ado_result.returncode != 0
    combined_ado = (ado_result.stderr + ado_result.stdout).lower()
    assert "operator-only" in combined_ado or "notimplementederror" in combined_ado
