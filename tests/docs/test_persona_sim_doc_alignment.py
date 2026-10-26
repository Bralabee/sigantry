"""Doc and starter/demo contract tests for persona-sim alignment (Wave 5).

Pins the documentation and template invariants for:
- Starter QUICKSTART.md CLI flag accuracy (no `--env`, no `deploy run --dry-run`,
  `--environment DEV`, and `sync.yml` / `fabric_items/` prerequisites before
  `preflight` or `deploy validate` / `deploy run`).
- Starter and Demo BRANCHING.md forward `sigantry deploy run` command example.
- Tutorial 07 (`07-rollback.md`) tenant pinning (`--tenant-id` / `AZURE_TENANT_ID`)
  and `sigantry release verify` ledger verification.
- PR-bot runbook (`pr-bot-operator.md`), Tutorial 09 (`09-pr-review-bot.md`),
  and `CAPABILITIES.md` alignment with the expanded TMDL parser (`role`,
  `perspective`, `expression`, `hierarchy`, `calculationGroup`,
  `calculationItem`), `.SemanticModel` + `.Lakehouse` deep diff scope vs
  non-Lakehouse `.platform` path trigger, and `pull_request` vs
  `pull_request_target` fork security trade-offs.
- `parameters-yml.md` case-sensitivity documentation for environment keys vs
  case-insensitive `_ALL_`.
- `dual-ci-strategy.md` expression-interpolation guard reference and
  `scheduled-drift.md` partial-governance `--fail-on-drift` guidance.
"""

from __future__ import annotations

from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]


def test_starter_quickstart_uses_valid_cli_flags_and_explains_prereqs() -> None:
    """templates/starter/docs/QUICKSTART.md uses valid CLI flags and explains sync.yml / fabric_items prerequisites."""
    qs = (_REPO_ROOT / "templates" / "starter" / "docs" / "QUICKSTART.md").read_text(
        encoding="utf-8"
    )
    assert "--env " not in qs, "QUICKSTART.md must use --environment, not non-existent --env"
    assert "sigantry deploy run --dry-run" not in qs, (
        "sigantry deploy run has no --dry-run flag; use sigantry deploy validate"
    )
    assert "sigantry config validate parameters.yml --environment DEV" in qs
    assert "sigantry deploy validate" in qs
    assert "sigantry preflight" in qs
    assert "sync.yml" in qs


def test_branching_docs_include_forward_deploy_run_example() -> None:
    """Starter and demo BRANCHING.md show the forward `sigantry deploy run --source` command alongside rollback."""
    for rel in (
        Path("templates/starter/docs/BRANCHING.md"),
        Path("templates/demo/docs/BRANCHING.md"),
    ):
        text = (_REPO_ROOT / rel).read_text(encoding="utf-8")
        assert "sigantry deploy run --source ./fabric_items" in text, (
            f"{rel} must show forward `sigantry deploy run --source ./fabric_items`"
        )


def test_tutorial_07_rollback_documents_tenant_pin_and_release_verify() -> None:
    """docs/tutorials/07-rollback.md documents tenant pinning on deploy run and runs `sigantry release verify`."""
    import re

    tut07 = (_REPO_ROOT / "docs" / "tutorials" / "07-rollback.md").read_text(encoding="utf-8")
    assert "AZURE_TENANT_ID" in tut07
    assert "sigantry release verify" in tut07
    assert "CHAIN VALID" in tut07

    deploy_blocks = re.findall(r"^sigantry deploy run(?:\\\n|[^\n])+", tut07, flags=re.MULTILINE)
    assert len(deploy_blocks) == 3, f"expected 3 deploy run invocations; got {len(deploy_blocks)}"
    for block in deploy_blocks:
        assert "--tenant-id" in block, f"deploy run block missing --tenant-id:\n{block}"

    record_blocks = re.findall(
        r"^sigantry release record(?:\\\n|[^\n])+", tut07, flags=re.MULTILINE
    )
    assert len(record_blocks) == 2
    for block in record_blocks:
        assert "--tenant-id" not in block, (
            f"sigantry release record has no --tenant-id flag (only --ado-tenant-id):\n{block}"
        )


def test_pr_bot_docs_reflect_expanded_tmdl_coverage_and_fork_tradeoffs() -> None:
    """pr-bot-operator.md and 09-pr-review-bot.md reflect expanded TMDL object support and fork security."""
    runbook = (_REPO_ROOT / "docs" / "runbooks" / "pr-bot-operator.md").read_text(encoding="utf-8")
    tut09 = (_REPO_ROOT / "docs" / "tutorials" / "09-pr-review-bot.md").read_text(encoding="utf-8")

    # Expanded TMDL kinds are now parsed:
    for kind in ("calculationGroup", "calculationItem", "hierarchy", "role", "perspective"):
        assert kind in runbook, f"pr-bot-operator.md must list {kind!r} among parsed TMDL kinds"
    assert "roles, perspectives" not in tut09, (
        "09-pr-review-bot.md must not claim roles and perspectives are unparsed"
    )
    # Fork PR / pull_request_target security trade-off documented in runbook:
    assert "pull_request_target" in runbook
    # Non-Lakehouse .platform clarification:
    normalized_runbook = " ".join(runbook.split()).lower()
    assert "non-lakehouse `.platform`" in normalized_runbook


def test_parameters_yml_doc_explains_case_sensitive_env_keys() -> None:
    """docs/reference/parameters-yml.md states that environment keys are case-sensitive while _ALL_ is case-insensitive."""
    params_doc = (_REPO_ROOT / "docs" / "reference" / "parameters-yml.md").read_text(
        encoding="utf-8"
    )
    assert "case-sensitive" in params_doc


def test_dual_ci_and_scheduled_drift_docs_cover_expression_guard_and_partial_governance() -> None:
    """dual-ci-strategy.md documents the expression-interpolation guard and scheduled-drift.md notes partial-governance drift."""
    dual_ci = (_REPO_ROOT / "docs" / "reference" / "dual-ci-strategy.md").read_text(
        encoding="utf-8"
    )
    drift_doc = (
        _REPO_ROOT / "docs" / "runbooks" / "drift-detection" / "scheduled-drift.md"
    ).read_text(encoding="utf-8")

    assert "test_pipeline_auth_and_pins.py" in dual_ci
    assert "shared workspace" in drift_doc or "subset of a workspace" in drift_doc
