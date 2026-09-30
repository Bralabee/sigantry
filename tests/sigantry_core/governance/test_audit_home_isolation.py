"""The test suite must never write to the real audit ledgers.

The root ``conftest.py`` points HOME and every default audit location at a
per-test temp directory (``_isolate_home_and_audit_dir``). Without it, a full
run appended fixture records (principals such as ``MockCredential``) to the
maintainer's real ``~/.sigantry/audit/`` ledgers on every run.

Falsifiability: deleting the fixture fails every test here -- the defaults
then resolve under the real home, outside pytest's base temp directory.
Dropping only the module patch fails the in-process tests; dropping only the
HOME override fails the subprocess test.
"""

from __future__ import annotations

import importlib
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest

from sigantry_core.governance.audit import emit_secret_change_record
from sigantry_core.governance.records import SecretChangeRecord

# Load every module that binds a default audit location at import time, so
# the scan below sees them whether or not other test modules ran first.
for _module in (
    "sigantry_core.deploy.rollback",
    "sigantry_core.governance.audit",
    "sigantry_core.governance.audit_io",
    "sigantry_core.release.cli",
    "sigantry_core.release.ledger",
    "sigantry_core.workspace.records",
):
    importlib.import_module(_module)

_DEFAULT_ATTRS = ("_DEFAULT_AUDIT_DIR", "DEFAULT_BOOTSTRAP_LEDGER")


def _inside(path: Path, root: Path) -> bool:
    return path.resolve().is_relative_to(root.resolve())


def test_every_loaded_default_audit_location_is_isolated(
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    base = tmp_path_factory.getbasetemp()
    found = {
        f"{name}.{attr}": getattr(module, attr)
        for name, module in list(sys.modules.items())
        if module is not None and (name == "sigantry_core" or name.startswith("sigantry_core."))
        for attr in _DEFAULT_ATTRS
        if hasattr(module, attr)
    }
    # The scan must see the known bindings, or it proves nothing.
    assert "sigantry_core.governance.audit_io._DEFAULT_AUDIT_DIR" in found
    assert "sigantry_core.workspace.records.DEFAULT_BOOTSTRAP_LEDGER" in found
    escaped = {k: str(v) for k, v in found.items() if not _inside(Path(v), base)}
    assert not escaped, f"default audit locations outside the test temp dir: {escaped}"


def test_default_emit_lands_in_the_isolated_home(
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    base = tmp_path_factory.getbasetemp()
    home = Path.home()
    assert _inside(home, base), f"HOME is not isolated: {home}"
    # Check where the emit will actually write BEFORE emitting, so a broken
    # isolation fails here instead of writing a probe into the real ledger.
    # (``sigantry_core.governance.audit`` as an attribute is a re-exported
    # function that shadows the submodule, hence import_module.)
    target = importlib.import_module("sigantry_core.governance.audit")._DEFAULT_AUDIT_DIR
    if not _inside(Path(target), base):
        pytest.fail(f"the default emit would write outside the test temp dir: {target}")
    emit_secret_change_record(
        SecretChangeRecord(
            operation="set",
            key="ISOLATION_PROBE",
            store_name="kv",
            actor="pytest",
            timestamp=datetime(2026, 1, 1, tzinfo=UTC),
        )
    )
    ledger = home / ".sigantry" / "audit" / "secret_changes.jsonl"
    assert ledger.is_file(), "a default-dir emit did not land in the isolated home"
    assert "ISOLATION_PROBE" in ledger.read_text(encoding="utf-8")


def test_subprocess_home_is_isolated(tmp_path_factory: pytest.TempPathFactory) -> None:
    out = subprocess.run(
        [sys.executable, "-c", "import pathlib; print(pathlib.Path.home())"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    assert _inside(Path(out), tmp_path_factory.getbasetemp()), (
        f"a subprocess sees the real home: {out}"
    )
