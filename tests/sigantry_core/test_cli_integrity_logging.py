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
import logging
import os
import subprocess
import sys
import textwrap
import unicodedata
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


def _ledger_with_edited_second_record(audit_dir: Path, second_release_id: str) -> Path:
    """Write ``R-good`` and ``second_release_id``, then edit the second after it was sealed."""
    emit_deploy_record(_record("R-good"), audit_dir=audit_dir)
    emit_deploy_record(_record(second_release_id), audit_dir=audit_dir)
    path = audit_dir / "deploys.jsonl"
    lines = path.read_text(encoding="utf-8").splitlines()
    obj = json.loads(lines[1])
    obj["approver"] = "someone-else@example.invalid"
    lines[1] = json.dumps(obj)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return audit_dir


@pytest.fixture
def tampered_ledger(tmp_path: Path) -> Path:
    """A two-record ledger whose second record was edited after it was sealed."""
    return _ledger_with_edited_second_record(tmp_path / "audit", "R-edited")


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


def _run_bytes(args: list[str], cwd: Path) -> subprocess.CompletedProcess[bytes]:
    """Like :func:`_run`, without newline translation, so every byte is checked."""
    return subprocess.run(
        [sys.executable, *args],
        cwd=cwd,
        env=_child_env(cwd),
        capture_output=True,
        timeout=120,
    )


#: Characters that end a line or drive a terminal: C0, DEL, C1 and the
#: Unicode line / paragraph separators all fall in these categories.
_LINE_OR_CONTROL = frozenset({"Cc", "Zl", "Zp"})


def _has_line_or_control(text: str) -> bool:
    return any(unicodedata.category(ch) in _LINE_OR_CONTROL for ch in text)


def test_a_ledger_value_prints_on_the_warning_line_with_its_escapes_visible(
    tmp_path: Path,
) -> None:
    """A release_id with a line feed and cursor controls cannot add or erase a line."""
    audit_dir = _ledger_with_edited_second_record(
        tmp_path / "audit", "R-2\nINFO all 2 ledger records verified\x1b[1A\x1b[2K"
    )
    result = _run_bytes(
        ["-m", "sigantry_core", "release", "list", "--audit-dir", str(audit_dir)], tmp_path
    )
    assert result.returncode == 0, result.stderr
    assert b"R-good" in result.stdout
    # Exactly one line: the WARNING, with the line feed and the escape
    # sequences shown as text rather than acted on.
    assert result.stderr.decode("utf-8") == (
        "WARNING sigantry_core.release.ledger: ledger_line_tampered lineno=2 "
        "release_id=R-2\\nINFO all 2 ledger records verified\\x1b[1A\\x1b[2K\n"
    )


def test_a_ledger_line_that_fails_the_schema_prints_one_line(tmp_path: Path) -> None:
    """The parser's multi-line error message is reported on one line."""
    audit_dir = tmp_path / "audit"
    emit_deploy_record(_record("R-good"), audit_dir=audit_dir)
    with (audit_dir / "deploys.jsonl").open("a", encoding="utf-8") as fh:
        fh.write(json.dumps({"release_id": "R-broken", "workspace": "ws-test"}) + "\n")
    result = _run_bytes(
        ["-m", "sigantry_core", "release", "list", "--audit-dir", str(audit_dir)], tmp_path
    )
    assert result.returncode == 0, result.stderr
    assert b"R-good" in result.stdout
    stderr = result.stderr.decode("utf-8")
    assert stderr.endswith("\n")
    line = stderr[:-1]
    assert not _has_line_or_control(line), repr(stderr)
    assert line.startswith(
        "WARNING sigantry_core.release.ledger: ledger_line_unparseable lineno=2 err="
    )
    # The error text is all there, its line breaks shown as \n.
    assert "validation errors for DeployRecord\\n" in line
    assert "Field required" in line


def test_the_formatter_escapes_every_line_break_and_control_character() -> None:
    from sigantry_core.cli import _IntegrityFormatter

    controls = "".join(chr(cp) for cp in (*range(0x20), *range(0x7F, 0xA0), 0x2028, 0x2029))
    record = logging.LogRecord(
        "sigantry_core.release.ledger",
        logging.WARNING,
        __file__,
        1,
        "ledger_line_tampered lineno=%d release_id=%s",
        (2, f"R-{controls}-end"),
        None,
    )
    try:
        raise ValueError("first\nsecond\u2028third")
    except ValueError:
        record.exc_info = sys.exc_info()
    line = _IntegrityFormatter().format(record)

    assert not _has_line_or_control(line), repr(line)
    assert line.splitlines() == [line]
    for shown in ("\\x00", "\\t", "\\n", "\\r", "\\x1b", "\\x7f", "\\x85", "\\x9b"):
        assert shown in line
    assert "\\u2028" in line and "\\u2029" in line
    assert line.endswith("-end (ValueError: first\\nsecond\\u2028third)")


def test_the_formatter_leaves_printable_text_alone() -> None:
    """Control: ordinary text, non-ASCII letters and backslashes print as they are."""
    from sigantry_core.cli import _IntegrityFormatter

    record = logging.LogRecord(
        "sigantry_core.release.ledger",
        logging.WARNING,
        __file__,
        1,
        "ledger_line_tampered lineno=%d release_id=%s",
        (2, "R-2 Zürich C:\\deploy"),
        None,
    )
    assert _IntegrityFormatter().format(record) == (
        "WARNING sigantry_core.release.ledger: ledger_line_tampered lineno=2 "
        "release_id=R-2 Zürich C:\\deploy"
    )


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


_FAILED_OPERATIONS_SCRIPT = textwrap.dedent(
    """
    import os
    import pathlib
    from unittest import mock

    import sigantry_core.cli as cli
    from sigantry_core.governance.destructive import destructive_op
    from sigantry_core.secrets.key_vault import KeyVaultSecretStore

    cli._install_integrity_log_handler()

    @destructive_op("workspace", "delete")
    def delete_ok(*, force=False, resource_id=None, principal=None):
        return "deleted"

    @destructive_op("workspace", "delete")
    def delete_fails(*, force=False, resource_id=None, principal=None):
        raise RuntimeError("service said no")

    # A destructive operation that succeeds and is recorded prints nothing.
    print(delete_ok(force=True, resource_id="ws-1", principal="tester"), flush=True)
    print("after-success", flush=True)
    try:
        delete_fails(force=True, resource_id="ws-2", principal="tester")
    except RuntimeError:
        print("delete-raised", flush=True)

    store = KeyVaultSecretStore(
        vault_url="https://vault.example.invalid/", credential=mock.MagicMock()
    )
    with mock.patch.object(store._client, "set_secret", side_effect=RuntimeError("no")):
        try:
            store.set("k", "v")
        except RuntimeError:
            print("secret-set-raised", flush=True)

    audit = pathlib.Path(os.environ["HOME"]) / ".sigantry" / "audit" / "destructive_ops.jsonl"
    print("destructive-records", len(audit.read_text(encoding="utf-8").splitlines()))
    """
)


def test_failed_audited_operations_print_one_line_each(tmp_path: Path) -> None:
    """A failed audited operation prints its one WARNING line; a success prints none."""
    result = _run(["-c", _FAILED_OPERATIONS_SCRIPT], cwd=tmp_path)
    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines() == [
        "deleted",
        "after-success",
        "delete-raised",
        "secret-set-raised",
        "destructive-records 2",
    ]
    assert result.stderr.strip().splitlines() == [
        "WARNING sigantry_core.governance.audit: destructive_op",
        "WARNING sigantry_core.governance.audit: secret_change_failed",
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
