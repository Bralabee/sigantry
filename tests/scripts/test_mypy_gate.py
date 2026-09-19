"""Unit tests for scripts/ci/mypy_gate.py.

The gate replaces the inert ``mypy sigantry_core/ || true`` step. Its value is
that it CAN fail -- on a new type error not present in the frozen baseline --
while tolerating position drift in the 63 accepted errors. These tests pin the
normalisation (position-stripping + multiset counting) and the new-error
residual, which are the properties that make the gate honest.
"""

from __future__ import annotations

import importlib.util
from collections import Counter
from pathlib import Path


def _load_gate():
    script_path = Path(__file__).resolve().parents[2] / "scripts" / "ci" / "mypy_gate.py"
    spec = importlib.util.spec_from_file_location("_mypy_gate", script_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_SAMPLE = """\
sigantry_core/deploy/cli.py:820: error: Argument 1 has incompatible type "X"  [arg-type]
sigantry_core/deploy/cli.py:944: error: Argument 1 has incompatible type "X"  [arg-type]
sigantry_core/sync/cli.py:253: error: Need type annotation for "envs_seen"  [var-annotated]
sigantry_core/sync/cli.py:253:12: note: See https://mypy.rtfd.io/some-note
Found 3 errors in 2 files (checked 132 source files)
"""


def test_normalise_strips_position_and_counts_multiset() -> None:
    gate = _load_gate()
    sigs = gate._normalise(_SAMPLE)
    # The two cli.py errors differ ONLY by line number -> one signature, count 2.
    arg_sig = 'sigantry_core/deploy/cli.py: error: Argument 1 has incompatible type "X"  [arg-type]'
    assert sigs[arg_sig] == 2
    # note: lines and the summary line are not errors.
    assert sum(sigs.values()) == 3
    assert all("note:" not in s and "Found" not in s for s in sigs)


def test_new_error_is_a_positive_residual_over_baseline() -> None:
    gate = _load_gate()
    current = gate._normalise(_SAMPLE)
    # Baseline that accepts ONE cli.py arg-type error; the second is "new".
    baseline: Counter[str] = Counter()
    for sig, n in current.items():
        baseline[sig] = n
    arg_sig = 'sigantry_core/deploy/cli.py: error: Argument 1 has incompatible type "X"  [arg-type]'
    baseline[arg_sig] = 1  # only one accepted; current has two

    new_errors = current - baseline
    assert new_errors[arg_sig] == 1
    assert sum(new_errors.values()) == 1


def test_no_new_errors_when_baseline_matches_current() -> None:
    gate = _load_gate()
    current = gate._normalise(_SAMPLE)
    baseline = Counter(dict(current))
    assert current - baseline == Counter()
