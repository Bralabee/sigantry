"""Audit-trail warnings reach stderr on the command line.

``import sigantry_core`` imports fabric-cicd, and fabric-cicd sets the root
logger to ERROR when it is imported. The ledger reader and the destructive-op
audit writer log at WARNING with no level of their own, so on the command
line ``ledger_line_tampered`` and ``destructive_op_audit_write_failed`` were
dropped before any handler saw them. :func:`sigantry_core.cli.main` gives
those two loggers a WARNING level and a stderr handler, and nothing else.

These tests run the real entry points in a child process, so the import-time
behaviour of whichever fabric-cicd release is installed (any release the
``fabric-cicd`` range in ``pyproject.toml`` admits) is part of what they
check: a release that changes how it resets logging is caught here.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap
from datetime import UTC, datetime
from pathlib import Path

import pytest

from sigantry_core.governance.audit import emit_deploy_record
from sigantry_core.release.record import DeployRecord

REPO_ROOT = Path(__file__).resolve().parents[2]


def _child_env(home: Path) -> dict[str, str]:
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join(
        [str(REPO_ROOT), *[p for p in env.get("PYTHONPATH", "").split(os.pathsep) if p]]
    )
    # Keep the child away from the operator's real ~/.sigantry.
    env["HOME"] = str(home)
    env["USERPROFILE"] = str(home)
    env["NO_COLOR"] = "1"
    return env


def _run(args: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, *args],
        cwd=cwd,
        env=_child_env(cwd),
        capture_output=True,
        text=True,
        timeout=120,
    )


def _record(release_id: str) -> DeployRecord:
    return DeployRecord(
        workspace="ws-test",
        release_id=release_id,
        work_items=[],
        fabric_items_changed=["nb_a.Notebook"],
        test_evidence={"smoke": "passed"},
        approver="alice@example.invalid",
        audit_hash="",
        created_at=datetime.now(UTC),
    ).with_hash()


@pytest.fixture
def tampered_ledger(tmp_path: Path) -> Path:
    """A two-record ledger whose second record was edited after it was sealed."""
    audit_dir = tmp_path / "audit"
    emit_deploy_record(_record("R-good"), audit_dir=audit_dir)
    emit_deploy_record(_record("R-edited"), audit_dir=audit_dir)
    path = audit_dir / "deploys.jsonl"
    lines = path.read_text(encoding="utf-8").splitlines()
    obj = json.loads(lines[1])
    obj["approver"] = "someone-else@example.invalid"
    lines[1] = json.dumps(obj)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return audit_dir


@pytest.mark.parametrize("module", ["sigantry_core", "sigantry_core.cli"])
def test_release_list_reports_an_edited_ledger_line_on_stderr(
    module: str, tampered_ledger: Path, tmp_path: Path
) -> None:
    result = _run(
        ["-m", module, "release", "list", "--audit-dir", str(tampered_ledger)],
        cwd=tmp_path,
    )
    assert result.returncode == 0, result.stderr
    # Control: the command really read the ledger.
    assert "R-good" in result.stdout
    assert "ledger_line_tampered" in result.stderr
    assert "release_id=R-edited" in result.stderr
    # Only the audit-trail event, not a traceback or other library noise.
    assert result.stderr.strip().splitlines() == [
        "WARNING sigantry_core.release.ledger: ledger_line_tampered lineno=2 release_id=R-edited"
    ]


def test_release_list_on_an_intact_ledger_prints_nothing_to_stderr(tmp_path: Path) -> None:
    audit_dir = tmp_path / "audit"
    emit_deploy_record(_record("R-one"), audit_dir=audit_dir)
    result = _run(
        ["-m", "sigantry_core", "release", "list", "--audit-dir", str(audit_dir)], tmp_path
    )
    assert result.returncode == 0, result.stderr
    assert "R-one" in result.stdout
    assert result.stderr == ""


_CHILD_SCRIPT = textwrap.dedent(
    """
    import importlib
    import logging
    from unittest import mock

    import sigantry_core.cli as cli
    from sigantry_core.governance.destructive import destructive_op

    cli._install_integrity_log_handler()
    cli._install_integrity_log_handler()  # idempotent: still one line per record

    # Scope: the handler sits on the two audit-trail loggers only. With the
    # root logger as importing the package left it, other loggers' warnings
    # stay off stderr, as do INFO records on the audit-trail loggers.
    print(
        "root-handlers-marked",
        sum(getattr(h, "_sigantry_cli_integrity", False) for h in logging.getLogger().handlers),
    )
    logging.getLogger("sigantry_core.sync.manifest").warning("other_sigantry_warning")
    logging.getLogger("azure.identity").warning("other_library_warning")
    logging.getLogger("sigantry_core.release.ledger").info("ledger_info_stays_quiet")

    # Whatever resets the root logger later, the two loggers keep their level.
    logging.getLogger().setLevel(logging.CRITICAL)

    audit = importlib.import_module("sigantry_core.governance.audit")

    @destructive_op("workspace", "delete")
    def delete_it(*, force=False, resource_id=None, principal=None):
        return "deleted"

    with mock.patch.object(
        audit, "emit_destructive_op_record", side_effect=OSError("disk full")
    ):
        print(delete_it(force=True, resource_id="ws-1", principal="tester"))
    """
)


def test_audit_write_failure_reaches_stderr_and_nothing_else_does(tmp_path: Path) -> None:
    result = _run(["-c", _CHILD_SCRIPT], cwd=tmp_path)
    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines() == ["root-handlers-marked 0", "deleted"]
    lines = result.stderr.strip().splitlines()
    assert lines == [
        "WARNING sigantry_core.governance.audit: destructive_op_audit_write_failed "
        "(OSError: disk full)"
    ]


def test_importing_the_cli_changes_no_logging_configuration(tmp_path: Path) -> None:
    """Library hosts that import the package keep their own configuration."""
    script = textwrap.dedent(
        """
        import logging
        import sigantry_core.cli  # noqa: F401

        for name in ("sigantry_core.release.ledger", "sigantry_core.governance.audit"):
            lg = logging.getLogger(name)
            print(name, lg.level, len(lg.handlers))
        """
    )
    result = _run(["-c", script], cwd=tmp_path)
    assert result.returncode == 0, result.stderr
    assert result.stdout.split("\n")[:2] == [
        "sigantry_core.release.ledger 0 0",
        "sigantry_core.governance.audit 0 0",
    ]


def test_console_script_target_is_main() -> None:
    """The installed ``sigantry`` script must go through ``main``, not ``app``."""
    import tomllib

    scripts = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"][
        "scripts"
    ]
    assert scripts["sigantry"] == "sigantry_core.cli:main"
