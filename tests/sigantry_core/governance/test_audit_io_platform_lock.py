"""Windows-portability tests for the audit-chain lock (tester report 2026-06-12).

A Windows tester running ``sigantry`` commands from the v3.2.0 wheel hit
``ModuleNotFoundError: No module named 'fcntl'``: ``audit_io`` imported
``fcntl`` unconditionally at module scope, and the governance audit plane
is imported transitively by effectively every CLI entry point, so the
toolkit was unusable on Windows at import time -- before the documented
"Windows accepts unserialised behaviour" intent could ever apply.

Post-fix: ``fcntl`` / ``msvcrt`` are imported behind ImportError guards
and :func:`audit_io._acquire_file_lock` / ``_release_file_lock`` pick the
platform primitive at call time (``fcntl.flock`` on POSIX,
``msvcrt.locking`` on Windows, unserialised no-op if neither exists).

Falsifiability:

- Reverting the guarded import (back to a bare ``import fcntl``) flips
  ``test_module_imports_when_fcntl_is_unavailable`` from PASS to FAIL --
  the subprocess dies with the tester's exact ``ModuleNotFoundError``.
- Dropping the msvcrt branch from ``_acquire_file_lock`` flips
  ``test_windows_branch_locks_and_unlocks_via_msvcrt`` -- the recorded
  call list comes back empty (silent no-op on Windows).
- Removing the contention-retry loop flips
  ``test_windows_branch_retries_on_contention`` -- the first simulated
  ``EDEADLK`` would propagate instead of being retried.
"""

from __future__ import annotations

import errno
import subprocess
import sys
from pathlib import Path

import pytest

from sigantry_core.governance import audit_io


def test_module_imports_when_fcntl_is_unavailable() -> None:
    """``audit_io`` must import on platforms without ``fcntl`` (Windows).

    Simulate Windows in a fresh interpreter by poisoning
    ``sys.modules['fcntl']`` BEFORE the toolkit import: a ``None`` entry
    makes ``import fcntl`` raise ImportError, which is exactly what
    CPython on Windows does. ``msvcrt`` is also absent here (POSIX test
    host), so this additionally pins that the guard tolerates BOTH
    primitives being unavailable.
    """
    code = (
        "import sys; sys.modules['fcntl'] = None; "
        "import sigantry_core.governance.audit_io as m; "
        "assert m.fcntl is None; print('import-ok')"
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert result.returncode == 0, (
        "audit_io failed to import without fcntl -- the Windows "
        f"ModuleNotFoundError regression is back:\n{result.stderr}"
    )
    assert "import-ok" in result.stdout


class _FakeMsvcrt:
    """Recording stand-in for the ``msvcrt`` module on a POSIX test host.

    Uses the real module's constant values (``LK_UNLCK=0``, ``LK_LOCK=1``)
    so assertions read like the Windows call site.
    """

    LK_UNLCK = 0
    LK_LOCK = 1

    def __init__(self, contention_failures: int = 0) -> None:
        self.calls: list[tuple[int, int, int]] = []
        self._remaining_failures = contention_failures

    def locking(self, fd: int, mode: int, nbytes: int) -> None:
        self.calls.append((fd, mode, nbytes))
        if mode == self.LK_LOCK and self._remaining_failures > 0:
            self._remaining_failures -= 1
            raise OSError(errno.EDEADLK, "resource deadlock avoided")


def test_windows_branch_locks_and_unlocks_via_msvcrt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With fcntl absent, the lock goes through msvcrt: LK_LOCK then LK_UNLCK, 1 byte each."""
    fake = _FakeMsvcrt()
    monkeypatch.setattr(audit_io, "fcntl", None)
    monkeypatch.setattr(audit_io, "msvcrt", fake)

    with audit_io.audit_chain_lock(tmp_path / "ledger.jsonl"):
        pass

    modes = [mode for _, mode, _ in fake.calls]
    assert modes == [fake.LK_LOCK, fake.LK_UNLCK], (
        f"expected exactly one lock then one unlock via msvcrt, got modes={modes}"
    )
    assert all(nbytes == 1 for _, _, nbytes in fake.calls), (
        "msvcrt.locking must lock exactly 1 byte at offset 0"
    )


def test_windows_branch_retries_on_contention(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Contention errnos (EDEADLK/EACCES from LK_LOCK's internal timeout) are retried.

    ``msvcrt.locking(LK_LOCK)`` raises after ~10 s of internal retries;
    the toolkit loops on that to match the blocking ``flock`` semantics
    on POSIX. Two simulated contention failures must yield three LK_LOCK
    attempts and then a normal unlock.
    """
    fake = _FakeMsvcrt(contention_failures=2)
    monkeypatch.setattr(audit_io, "fcntl", None)
    monkeypatch.setattr(audit_io, "msvcrt", fake)

    with audit_io.audit_chain_lock(tmp_path / "ledger.jsonl"):
        pass

    lock_attempts = [m for _, m, _ in fake.calls if m == fake.LK_LOCK]
    assert len(lock_attempts) == 3, (
        f"expected 3 LK_LOCK attempts (2 contention failures + 1 success), got {len(lock_attempts)}"
    )
    assert fake.calls[-1][1] == fake.LK_UNLCK


def test_windows_branch_reraises_non_contention_errors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A non-contention OSError (e.g. EBADF) must propagate, not loop forever."""

    class _BrokenMsvcrt(_FakeMsvcrt):
        def locking(self, fd: int, mode: int, nbytes: int) -> None:
            raise OSError(errno.EBADF, "bad file descriptor")

    monkeypatch.setattr(audit_io, "fcntl", None)
    monkeypatch.setattr(audit_io, "msvcrt", _BrokenMsvcrt())

    with (
        pytest.raises(OSError, match="bad file descriptor"),
        audit_io.audit_chain_lock(tmp_path / "ledger.jsonl"),
    ):
        pass


def test_no_lock_primitive_degrades_to_unserialised(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Neither fcntl nor msvcrt available: the lock is a no-op, not a crash.

    No real CPython platform hits this, but the guard must not turn an
    exotic runtime into an audit-plane outage -- unserialised writes are
    the documented degraded mode.
    """
    monkeypatch.setattr(audit_io, "fcntl", None)
    monkeypatch.setattr(audit_io, "msvcrt", None)

    with audit_io.audit_chain_lock(tmp_path / "ledger.jsonl"):
        pass
