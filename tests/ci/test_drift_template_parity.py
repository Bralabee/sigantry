"""Real assertions for the scheduled drift-check pair (Plan 13-06 / DRIFT-03).

Replaces the five Wave 0 xfail stubs from Plan 13-00 with bodies that
read the real ADO + GHA YAML and confirm the locked decisions D-27
(parameter shape), D-28 (env-block input threading -- Pitfall 8
inheritance), D-29 (dual-CI parity-lint contract), and D-30
(notification-sink hook).

Invariants tested:

1. Pair existence (Wave 0 invariant; kept as the entry-point check).
2. No exception annotation on the pair (the second non-exception pair
   under the dual-ci parity lint, after Phase 12 PIPELINE-01/02).
3. Inputs declared on both sides (with the documented divergences
   covered by ``sigantry-dual-ci-ignore`` annotations).
4. Both halves invoke ``sigantry diff`` with ``--output json`` and
   ``--fail-on-drift`` (the CI-gating contract).
5. Both halves invoke ``python -m sigantry_core.sync._notify_main`` and
   reference the ``SIGANTRY_NOTIFICATION_SINK`` env var.
6. Operator inputs are threaded via ``env:`` blocks (Pitfall 8 / D-28).
7. ``scripts/ci/check-dual-ci-parity.py`` exits 0 against the real repo.
"""

from __future__ import annotations

import pathlib
import subprocess
import sys

import yaml

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
ADO_TEMPLATE = REPO_ROOT / "templates" / "schedules" / "drift-check.yml"
GHA_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "drift-check.yml"


# ---------------------------------------------------------------------------
# 1. Pair existence (Wave 0 invariant)
# ---------------------------------------------------------------------------


def test_drift_template_pair_exists() -> None:
    """Both paired YAML files must exist (Wave 0 placeholders OK; Plan 13-06 fills bodies)."""
    assert ADO_TEMPLATE.is_file(), f"missing: {ADO_TEMPLATE}"
    assert GHA_WORKFLOW.is_file(), f"missing: {GHA_WORKFLOW}"


# ---------------------------------------------------------------------------
# 2. No exception annotation (second non-exception pair)
# ---------------------------------------------------------------------------


def test_drift_pair_carries_no_dual_ci_exception() -> None:
    """Second non-exception pair (after Phase 12 PIPELINE-01/02)."""
    ado_text = ADO_TEMPLATE.read_text("utf-8")
    gha_text = GHA_WORKFLOW.read_text("utf-8")
    assert "sigantry-dual-ci-exception" not in ado_text, (
        "ADO drift-check template must NOT carry the ci-mechanics exception annotation"
    )
    assert "sigantry-dual-ci-exception" not in gha_text, (
        "GHA drift-check workflow must NOT carry the ci-mechanics exception annotation"
    )


# ---------------------------------------------------------------------------
# 3. Inputs declared on both sides (D-27)
# ---------------------------------------------------------------------------


def _gha_inputs(doc: dict) -> dict[str, dict]:
    """Return the GHA ``workflow_call.inputs`` mapping.

    PyYAML 1.1 parses the bare ``on`` key as Python ``True`` (YAML 1.1
    truthy alias). Look up both spellings to stay robust regardless of
    how the workflow file is authored.
    """
    on = doc.get("on", doc.get(True))
    if not isinstance(on, dict):
        return {}
    wc = on.get("workflow_call")
    if not isinstance(wc, dict):
        return {}
    inputs = wc.get("inputs")
    return dict(inputs) if isinstance(inputs, dict) else {}


def test_drift_pair_declares_drift_check_inputs() -> None:
    """workspaceId / environment / manifestPath / notificationSink / cron declared on both halves (D-27)."""
    ado_doc = yaml.safe_load(ADO_TEMPLATE.read_text("utf-8"))
    gha_doc = yaml.safe_load(GHA_WORKFLOW.read_text("utf-8"))

    ado_param_names = {p["name"] for p in ado_doc.get("parameters", [])}
    gha_input_names = set(_gha_inputs(gha_doc).keys())

    # Cross-CI common inputs MUST appear on both halves.
    common = {"workspaceId", "environment", "manifestPath", "notificationSink", "cron"}
    assert common.issubset(ado_param_names), (
        f"ADO parameters missing common inputs: {common - ado_param_names}"
    )
    assert common.issubset(gha_input_names), (
        f"GHA workflow_call.inputs missing common inputs: {common - gha_input_names}"
    )

    # ADO carries the extra ``serviceConnection`` parameter, excused by the
    # ``gha-uses-oidc-not-service-connection`` shared ignore annotation in
    # both file headers (verified by the dual-CI parity lint).
    assert "serviceConnection" in ado_param_names, (
        "ADO drift-check should declare a serviceConnection parameter"
    )


# ---------------------------------------------------------------------------
# 4. sigantry diff invocation (D-26 + DRIFT-03)
# ---------------------------------------------------------------------------


def test_drift_pair_invokes_sigantry_diff_with_fail_on_drift() -> None:
    """Both halves run ``sigantry diff --output json --fail-on-drift`` (DRIFT-03)."""
    ado_text = ADO_TEMPLATE.read_text("utf-8")
    gha_text = GHA_WORKFLOW.read_text("utf-8")
    for label, text in (("ADO", ado_text), ("GHA", gha_text)):
        assert "sigantry diff" in text, f"{label} half must invoke `sigantry diff`"
        assert "--output json" in text, (
            f"{label} half must pass `--output json` (D-26 wire contract)"
        )
        assert "--fail-on-drift" in text, (
            f"{label} half must pass `--fail-on-drift` (D-26 CI gate contract)"
        )


# ---------------------------------------------------------------------------
# 5. Notification-sink hook (D-30)
# ---------------------------------------------------------------------------


def test_drift_pair_has_notification_sink_hook() -> None:
    """Both halves invoke `_notify_main` and reference SIGANTRY_NOTIFICATION_SINK (D-30)."""
    ado_text = ADO_TEMPLATE.read_text("utf-8")
    gha_text = GHA_WORKFLOW.read_text("utf-8")
    for label, text in (("ADO", ado_text), ("GHA", gha_text)):
        assert "sigantry_core.sync._notify_main" in text, (
            f"{label} half must invoke the python -m sigantry_core.sync._notify_main entrypoint"
        )
        assert "SIGANTRY_NOTIFICATION_SINK" in text, (
            f"{label} half must thread SIGANTRY_NOTIFICATION_SINK via env block"
        )


# ---------------------------------------------------------------------------
# 6. env-block input threading (D-28 / Pitfall 8 inheritance)
# ---------------------------------------------------------------------------


def _ado_drift_check_env(doc: dict) -> dict:
    """Return the ``env:`` mapping of the ADO ``drift_check`` job's AzureCLI step."""
    for stage in doc.get("stages", []):
        if not isinstance(stage, dict) or stage.get("stage") != "drift_check":
            continue
        for job in stage.get("jobs", []):
            if not isinstance(job, dict) or job.get("job") != "drift_check":
                continue
            for step in job.get("steps", []):
                if not isinstance(step, dict):
                    continue
                env = step.get("env")
                if isinstance(env, dict):
                    return env
    return {}


def _gha_drift_check_env(doc: dict) -> dict:
    """Return the ``env:`` mapping of the GHA ``drift_check`` job."""
    jobs = doc.get("jobs")
    if not isinstance(jobs, dict):
        return {}
    job = jobs.get("drift_check")
    if not isinstance(job, dict):
        return {}
    env = job.get("env")
    return dict(env) if isinstance(env, dict) else {}


def test_drift_pair_uses_env_block_for_inputs() -> None:
    """D-28 (Pitfall 8 inheritance from PIPELINE-02): operator inputs threaded via env: block, no shell-injection."""
    ado_doc = yaml.safe_load(ADO_TEMPLATE.read_text("utf-8"))
    gha_doc = yaml.safe_load(GHA_WORKFLOW.read_text("utf-8"))

    ado_env = _ado_drift_check_env(ado_doc)
    gha_env = _gha_drift_check_env(gha_doc)

    required_env_keys = {
        "SIGANTRY_DRIFT_WORKSPACE_ID",
        "SIGANTRY_DRIFT_MANIFEST_PATH",
        "SIGANTRY_DRIFT_ENVIRONMENT",
    }
    missing_ado = required_env_keys - set(ado_env)
    missing_gha = required_env_keys - set(gha_env)
    assert not missing_ado, f"ADO drift_check step missing env keys: {missing_ado}"
    assert not missing_gha, f"GHA drift_check job missing env keys: {missing_gha}"


# ---------------------------------------------------------------------------
# 7. Dual-CI parity lint pass (D-29)
# ---------------------------------------------------------------------------


def test_dual_ci_parity_lint_passes_with_drift_pair() -> None:
    """check-dual-ci-parity.py exits 0 against the real repo after Plan 13-06 lands both halves."""
    script = REPO_ROOT / "scripts" / "ci" / "check-dual-ci-parity.py"
    assert script.is_file(), f"parity lint missing: {script}"
    result = subprocess.run(
        [sys.executable, str(script), "--root", str(REPO_ROOT), "--verbose"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, (
        f"dual-CI parity lint failed (returncode={result.returncode})\n"
        f"--- stdout ---\n{result.stdout}\n--- stderr ---\n{result.stderr}"
    )
    # The drift-check pair MUST appear in the verbose output.
    assert "drift-check.yml" in result.stdout, (
        f"drift-check pair did not appear in dual-CI parity verbose output:\n{result.stdout}"
    )
