"""Demo CI workflow-pair parity test (Plan 15-03 / DEMO-02 CI half).

Mirror of tests/ci/test_starter_dual_ci.py. CONTEXT D-05: the demo
pair lives under templates/demo/.{github,azuredevops}/ and is
annotated with sigantry-dual-ci-exception: ci-mechanics so the
monorepo dual-CI parity registry skips it; this phase-specific
test enforces parity instead.

Six tests after the Plan 15-03 flip:

1. test_both_files_exist                                      -- pair on disk.
2. test_gha_paths_filter_includes_fabric_items_and_parameters_yml
                                                              -- GHA paths.
3. test_ado_paths_include_fabric_items_and_parameters_yml     -- ADO paths.
4. test_both_invoke_three_sigantry_commands                   -- 3-command parity.
5. test_export_demo_dry_run_clean_with_demo_ci_yamls_committed
                                                              -- export-demo
                                                                 parity gate
                                                                 still passes
                                                                 with the new
                                                                 YAMLs in tree.
6. test_ado_file_carries_ci_mechanics_exception_annotation    -- annotation
                                                                 in first 5
                                                                 lines per
                                                                 EXCEPTION_PATTERN.

Wave 0 (Plan 15-00) stamped four xfail stubs; Plan 15-03 fills the
bodies after the workflow YAML pair lands.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[2]
GHA = REPO / "templates" / "demo" / ".github" / "workflows" / "sigantry-demo-ci.yml"
ADO = REPO / "templates" / "demo" / ".azuredevops" / "sigantry-demo-ci.yml"

REQUIRED_PATHS_FILTER = ("fabric_items/**", "parameters.yml")
REQUIRED_CMDS = ("sigantry deploy run", "sigantry release record", "sigantry diff")


def _gha_on(doc: object) -> dict:
    """PyYAML 1.1 parses unquoted ``on:`` as Python True; defend.

    Mirrors the lookup discipline from
    ``scripts/ci/check-dual-ci-parity.py:_gha_on`` and
    ``tests/ci/test_starter_dual_ci.py:_gha_on``.
    """
    if not isinstance(doc, dict):
        return {}
    if "on" in doc:
        val = doc["on"]
    elif True in doc:
        val = doc[True]
    else:
        return {}
    return val if isinstance(val, dict) else {}


def test_both_files_exist() -> None:
    """Plan 15-03 ships templates/demo/.github/workflows/sigantry-demo-ci.yml + .azuredevops/sigantry-demo-ci.yml."""
    assert GHA.is_file(), GHA
    assert ADO.is_file(), ADO


def test_gha_paths_filter_includes_fabric_items_and_parameters_yml() -> None:
    """GHA workflow's on.push.paths includes fabric_items/** and parameters.yml."""
    doc = yaml.safe_load(GHA.read_text())
    on = _gha_on(doc)
    paths = on["push"]["paths"]
    for p in REQUIRED_PATHS_FILTER:
        assert p in paths, f"GHA missing path filter {p!r}; got {paths!r}"


def test_ado_paths_include_fabric_items_and_parameters_yml() -> None:
    """ADO pipeline's trigger.paths.include references fabric_items and parameters.yml."""
    txt = ADO.read_text()
    for p in REQUIRED_PATHS_FILTER:
        # ADO YAML lists items as "include: [fabric_items, parameters.yml]" --
        # match the bare token (strip trailing /** wildcard).
        bare = p.rstrip("/*").rstrip("/")
        assert bare in txt, f"ADO missing path include {bare!r}"


def test_both_invoke_three_sigantry_commands() -> None:
    """Both workflow halves invoke the same 3 commands: sigantry deploy run, release record, diff (D-04)."""
    gha_txt = GHA.read_text()
    ado_txt = ADO.read_text()
    for cmd in REQUIRED_CMDS:
        assert cmd in gha_txt, f"GHA missing {cmd!r}"
        assert cmd in ado_txt, f"ADO missing {cmd!r}"


def test_export_demo_dry_run_clean_with_demo_ci_yamls_committed() -> None:
    """Confirms the demo content + the new YAML pair together still pass export-demo's parity gate.

    Plan 15-01 introduced ``scripts/export-demo.py --dry-run`` as the
    parity gate that asserts ``templates/demo/`` is internally
    consistent. Plan 15-03 adds two new YAML files to the demo tree;
    this test ratifies that the parity gate still exits 0 with the
    new files in place.
    """
    result = subprocess.run(
        [sys.executable, str(REPO / "scripts" / "export-demo.py"), "--dry-run"],
        cwd=REPO,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, (
        f"export-demo --dry-run failed (exit {result.returncode}):\n"
        f"--- stdout ---\n{result.stdout}\n--- stderr ---\n{result.stderr}"
    )


def test_ado_file_carries_ci_mechanics_exception_annotation() -> None:
    """Annotation must appear in first 5 lines per scripts/ci/check-dual-ci-parity.py EXCEPTION_PATTERN.

    The ADO file lives under ``templates/demo/.azuredevops/`` which is
    OUTSIDE the dual-CI parity walker's current scan tree
    (``ADO_SUBDIRS = stages/schedules/pr-review``), but the annotation
    is required as defence-in-depth so any future extension of the
    walker correctly skips this adopter-consumed file (RESEARCH
    Anti-Pattern 4 + Phase 14 pr-bot.yml precedent).
    """
    head = ADO.read_text().splitlines()[:5]
    head_blob = "\n".join(head)
    assert "sigantry-dual-ci-exception: ci-mechanics" in head_blob, (
        f"annotation missing in first 5 lines of {ADO}; got:\n{head_blob}"
    )
