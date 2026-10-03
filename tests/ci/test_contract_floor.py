"""The seam contract suite must execute exactly ``CONTRACT_FLOOR`` tests.

ADR-0016 ("Contract-suite skip policy") decided that a contract arm may skip
where its implementation is not installed, but that the skip must not be
silent. This test runs the whole ``tests/contract/`` directory in a child
pytest, reads its JUnit XML report, and fails unless exactly
``CONTRACT_FLOOR`` tests executed:

* fewer -- a skip, an error (at collection or fixture setup) or a removed
  test lowered the count;
* more -- contract tests were added and ``CONTRACT_FLOOR`` was not raised.

Equality, not a minimum: with ``>=`` a suite that grew past the floor could
later lose that many tests with a green build. A failing contract test still
counts as executed (the main run reports the failure itself).

The child always runs the full directory, so this holds however the parent
run was scoped (``pytest tests/ci``, ``-k``, a single file).

The count is the same on every CI leg: no contract test carries a
platform-dependent skip condition. The only skip conditions left in
``tests/contract/`` are ``importorskip`` calls for ``nacl`` (pynacl, a runtime
dependency in ``pyproject.toml``) and for modules inside ``sigantry_core``.
"""

from __future__ import annotations

import os
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
CONTRACT_DIR = REPO_ROOT / "tests" / "contract"

#: Contract tests that execute on a clean runner (``pip install -e '.[test]'``,
#: no plugin distribution installed). A change that adds or removes a contract
#: test updates this number IN THE SAME COMMIT (ADR-0016).
CONTRACT_FLOOR = 94

# pytest exit codes that still leave a complete JUnit report: 0 all passed,
# 1 some failed, 2 interrupted (a collection error), 5 nothing collected.
# 3 (internal error) and 4 (usage error) are harness failures, never a count.
_COUNTABLE_RC = (0, 1, 2, 5)


def test_contract_suite_executes_exactly_the_floor(tmp_path: Path) -> None:
    report = tmp_path / "contract.xml"
    # PYTEST_ADDOPTS is cleared so a developer's -k/-x/--lf cannot shrink the
    # child run; the project addopts in pyproject.toml still apply, as in CI.
    env = {**os.environ, "PYTEST_ADDOPTS": ""}
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            str(CONTRACT_DIR),
            "-p",
            "no:cacheprovider",
            f"--basetemp={tmp_path / 'basetemp'}",
            f"--junitxml={report}",
        ],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=300,
        check=False,
    )
    tail = (
        f"\n--- child stdout (tail) ---\n{proc.stdout[-3000:]}"
        f"\n--- child stderr (tail) ---\n{proc.stderr[-2000:]}"
    )
    assert proc.returncode in _COUNTABLE_RC and report.is_file(), (
        f"child pytest could not run tests/contract (rc={proc.returncode}); "
        "this is a harness failure, not a count." + tail
    )
    root = ET.parse(report).getroot()
    suites = [root] if root.tag == "testsuite" else list(root.iter("testsuite"))
    # JUnit ``tests`` counts passed + failed + skipped + errors.
    total = sum(int(s.get("tests", "0")) for s in suites)
    skipped = sum(int(s.get("skipped", "0")) for s in suites)
    errors = sum(int(s.get("errors", "0")) for s in suites)
    executed = total - skipped - errors
    if errors:
        hint = (
            "An error (at collection or fixture setup) never counts as executed: "
            "fix it rather than lowering CONTRACT_FLOOR."
        )
    else:
        hint = (
            f"If the change is intended, set CONTRACT_FLOOR = {executed} in "
            f"{Path(__file__).name} in the same commit (ADR-0016)."
        )
    assert executed == CONTRACT_FLOOR, (
        f"Contract floor: tests/contract executed {executed} tests (collected {total}, "
        f"skipped {skipped}, errors {errors}); CONTRACT_FLOOR is {CONTRACT_FLOOR}. "
        "Fewer means a skip, an error or a removed test lowered the count; more "
        "means contract tests were added. " + hint + tail
    )
