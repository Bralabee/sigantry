#!/usr/bin/env python3
"""Enforcing mypy gate with a position-independent baseline.

CI previously ran ``mypy sigantry_core/ || true`` -- a check that can never
fail certifies nothing, while CONTRIBUTING.md claimed mypy was "clean". There
are 63 pre-existing errors in the tree (measured 2026-08-14, mypy 1.20.2). This
gate freezes those 63 as an accepted baseline and fails the build only on NEW
errors, so the type surface can be burned down over time without a big-bang
cleanup and without letting fresh regressions in silently.

How it works:
    Each mypy ``error:`` line is normalised by stripping its ``:line[:col]:``
    position, leaving ``<path>: error: <message>  [<code>]``. Positions drift
    as unrelated code moves, so baselining by position would false-fail on
    every refactor; baselining by (path, message, code) is stable. Errors are
    counted as a multiset, so N identical errors in a file require N baseline
    entries -- adding an (N+1)th genuinely-new one fails.

    A NEW error is any normalised signature whose current count exceeds its
    baseline count. Baseline entries that no longer appear (errors that were
    fixed) do NOT fail the build; they print a hint to regenerate so the
    baseline ratchets downward and can never silently re-admit a fixed error.

Usage:
    python scripts/ci/mypy_gate.py                 # enforce (CI)
    python scripts/ci/mypy_gate.py --update        # regenerate the baseline

Exit codes:
    0 -- no new errors (baseline satisfied).
    1 -- at least one new (non-baselined) error.
    2 -- mypy could not be run.
"""

from __future__ import annotations

import re
import subprocess
import sys
from collections import Counter
from pathlib import Path

_TARGET = "sigantry_core/"
_BASELINE_PATH = Path(__file__).resolve().parents[2] / "mypy-baseline.txt"

# <path>:<line>[:<col>]: error: <message>  [<code>]
_ERROR_RE = re.compile(r"^(?P<path>[^:]+):\d+(?::\d+)?: error: (?P<rest>.*)$")


def _run_mypy() -> str:
    """Run ``mypy sigantry_core/`` and return its combined stdout+stderr text.

    mypy exits non-zero when it finds errors; that is expected here (we parse
    the output rather than trust the exit code). A missing mypy / crash is the
    only failure we surface as rc=2.
    """
    try:
        proc = subprocess.run(
            [sys.executable, "-m", "mypy", _TARGET],
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError as exc:  # pragma: no cover - environment failure
        print(f"mypy_gate: could not run mypy: {exc}", file=sys.stderr)
        raise SystemExit(2) from exc
    return (proc.stdout or "") + (proc.stderr or "")


def _normalise(output: str) -> Counter[str]:
    """Return a multiset of position-stripped ``error:`` signatures."""
    sigs: Counter[str] = Counter()
    for line in output.splitlines():
        m = _ERROR_RE.match(line.rstrip())
        if m:
            sigs[f"{m.group('path')}: error: {m.group('rest')}"] += 1
    return sigs


def _load_baseline() -> Counter[str]:
    if not _BASELINE_PATH.exists():
        return Counter()
    sigs: Counter[str] = Counter()
    for raw in _BASELINE_PATH.read_text(encoding="utf-8").splitlines():
        line = raw.rstrip("\n")
        if not line or line.startswith("#"):
            continue
        sigs[line] += 1
    return sigs


def _write_baseline(sigs: Counter[str]) -> None:
    header = (
        "# mypy baseline -- position-stripped `error:` signatures accepted by\n"
        "# scripts/ci/mypy_gate.py. Regenerate with `python scripts/ci/mypy_gate.py\n"
        "# --update`. Never ADD to this file by hand to dodge a new error; fix the\n"
        "# error. This list should only ever shrink.\n"
    )
    body = "\n".join(sig for sig in sorted(sigs.elements()))
    _BASELINE_PATH.write_text(header + body + "\n", encoding="utf-8")


def main(argv: list[str]) -> int:
    update = "--update" in argv[1:]
    current = _normalise(_run_mypy())

    if update:
        _write_baseline(current)
        print(f"mypy_gate: baseline written with {sum(current.values())} error(s).")
        return 0

    baseline = _load_baseline()
    new_errors = current - baseline  # multiset residual (positive counts only)
    fixed = baseline - current

    if new_errors:
        print("mypy_gate: NEW type errors not in the baseline:")
        for sig, count in sorted(new_errors.items()):
            for _ in range(count):
                print(f"  {sig}")
        print(
            "\nFix the error above, or -- only if it is genuinely accepted -- "
            "regenerate the baseline with `python scripts/ci/mypy_gate.py --update`."
        )
        return 1

    if fixed:
        print(
            f"mypy_gate: {sum(fixed.values())} baselined error(s) no longer occur; "
            "run `python scripts/ci/mypy_gate.py --update` to ratchet the baseline down."
        )
    print(f"mypy_gate: clean against baseline ({sum(current.values())} known error(s), 0 new).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
