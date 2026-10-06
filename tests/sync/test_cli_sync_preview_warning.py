"""Plan 13-07 / W2 plan-checker resolution + SPEC §Constraints #1.

Locks the runtime gate behind ``workflow.preview_apis_acknowledged``: the
Phase 13 sync CLI emits a one-time WARNING on first ``sigantry sync apply``
or ``sigantry sync pull`` invocation per process when the flag is False
(default), and suppresses the warning when the flag is True.

Without this test, the docs/reference/api-stability.md claim that the gate
is enforced at runtime is unfalsifiable. With these three tests, a
regression where the warning silently never fires (or fires more than once
per process) is caught at CI time.

Council D #1: the Folders REST endpoint family is Preview as of Feb 2026;
operators acknowledge by setting the flag in `.sigantry.toml` (or via
the ``SIGANTRY_WORKFLOW__PREVIEW_APIS_ACKNOWLEDGED`` env var).
"""

from __future__ import annotations

import logging
import os
import sys
import warnings
from collections.abc import Iterator
from pathlib import Path
from typing import TextIO

import pytest
from typer.testing import CliRunner

from sigantry_core.config import _CONFIG_FILENAME, _LEGACY_CONFIG_FILENAME
from sigantry_core.sync import cli as sync_cli_mod
from sigantry_core.sync.cli import sync_app
from sigantry_core.sync.errors import ManifestValidationError

runner = CliRunner()


@pytest.fixture
def reset_preview_warning_emitted():
    """Reset the process-scoped warning sentinel between tests.

    The helper is module-level so the "fires only once per process"
    invariant must be exercised by clearing the sentinel BEFORE each test
    that exercises the warning surface, otherwise tests bleed into each
    other depending on collection order.
    """
    sync_cli_mod._PREVIEW_WARNING_EMITTED.clear()
    yield
    sync_cli_mod._PREVIEW_WARNING_EMITTED.clear()


def _stub_apply_to_raise_validation_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """Stub ``apply_sync`` so the warning emitter runs first, then the call
    short-circuits with a deterministic error.

    The warning is emitted at the very top of ``apply_cmd`` BEFORE the
    underlying engine is invoked. We don't need a working engine to assert
    the warning fires -- we just need the call path to reach the emitter.
    """

    def fake_apply(*_a, **_k):
        raise ManifestValidationError(
            "synthetic manifest-validation halt",
            violations=[],
        )

    monkeypatch.setattr(sync_cli_mod, "apply_sync", fake_apply)


def test_preview_warning_emits_when_flag_false(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    reset_preview_warning_emitted: None,
) -> None:
    """When ``preview_apis_acknowledged is False``, the WARNING fires once.

    Asserts:
    1. ``caplog`` captures exactly one WARNING-level record from the
       ``sigantry_core.sync.cli`` logger.
    2. The message body names the gate's TOML key + env var so operators
       grepping their CI logs land on the acknowledgement instructions.
    """
    # Default settings have preview_apis_acknowledged=False; we use a
    # synthetic manifest path so the underlying engine doesn't need to
    # exist (the stubbed apply_sync raises before touching disk).
    _stub_apply_to_raise_validation_error(monkeypatch)
    manifest = tmp_path / "synthetic-sync.yml"
    manifest.write_text("schema_version: '1.0.0'\nitems: []\n", encoding="utf-8")

    with caplog.at_level(logging.WARNING, logger="sigantry_core.sync.cli"):
        result = runner.invoke(
            sync_app,
            [
                "apply",
                "--manifest",
                str(manifest),
                "--workspace-id",
                "ws-synthetic",
            ],
        )

    # The stubbed apply raises ManifestValidationError -> exit 1.
    assert result.exit_code == 1, result.output

    preview_records = [
        r
        for r in caplog.records
        if r.name == "sigantry_core.sync.cli"
        and r.levelno == logging.WARNING
        and "preview" in r.message.lower()
    ]
    assert len(preview_records) == 1, (
        f"Expected exactly 1 preview-API WARNING record; "
        f"got {len(preview_records)}: "
        f"{[r.message for r in preview_records]}"
    )
    msg = preview_records[0].message
    assert "preview_apis_acknowledged" in msg
    assert "SIGANTRY_WORKFLOW__PREVIEW_APIS_ACKNOWLEDGED" in msg


def test_preview_warning_suppressed_when_flag_true(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    reset_preview_warning_emitted: None,
) -> None:
    """With ``preview_apis_acknowledged=True``, NO warning fires.

    Sets the env var (which beats the TOML default) and asserts the
    warning emission path short-circuits before the logger call.

    Deliberately still uses the legacy ``FDT_`` prefix: this is the
    end-to-end proof that the one-minor legacy env surface keeps working
    through the CLI, not just in the loader's own unit tests.
    """
    monkeypatch.setenv("FDT_WORKFLOW__PREVIEW_APIS_ACKNOWLEDGED", "true")
    _stub_apply_to_raise_validation_error(monkeypatch)
    manifest = tmp_path / "synthetic-sync.yml"
    manifest.write_text("schema_version: '1.0.0'\nitems: []\n", encoding="utf-8")

    with caplog.at_level(logging.WARNING, logger="sigantry_core.sync.cli"):
        result = runner.invoke(
            sync_app,
            [
                "apply",
                "--manifest",
                str(manifest),
                "--workspace-id",
                "ws-synthetic",
            ],
        )

    assert result.exit_code == 1, result.output  # ManifestValidationError stub

    preview_records = [
        r
        for r in caplog.records
        if r.name == "sigantry_core.sync.cli"
        and r.levelno == logging.WARNING
        and "preview" in r.message.lower()
    ]
    assert preview_records == [], (
        f"Preview warning leaked despite preview_apis_acknowledged=true: "
        f"{[r.message for r in preview_records]}"
    )


def test_preview_warning_emits_only_once_per_process(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    reset_preview_warning_emitted: None,
) -> None:
    """Two consecutive ``sigantry sync apply`` calls produce ONE warning.

    The module-level ``_PREVIEW_WARNING_EMITTED`` sentinel guarantees the
    operator gets one acknowledgement nudge per process, not one per
    invocation -- a CI loop running 100 sync applies emits one WARNING,
    not 100.
    """
    _stub_apply_to_raise_validation_error(monkeypatch)
    manifest = tmp_path / "synthetic-sync.yml"
    manifest.write_text("schema_version: '1.0.0'\nitems: []\n", encoding="utf-8")

    with caplog.at_level(logging.WARNING, logger="sigantry_core.sync.cli"):
        for _ in range(2):
            runner.invoke(
                sync_app,
                [
                    "apply",
                    "--manifest",
                    str(manifest),
                    "--workspace-id",
                    "ws-synthetic",
                ],
            )

    preview_records = [
        r
        for r in caplog.records
        if r.name == "sigantry_core.sync.cli"
        and r.levelno == logging.WARNING
        and "preview" in r.message.lower()
    ]
    assert len(preview_records) == 1, (
        f"Expected exactly 1 preview-API WARNING across 2 invocations; "
        f"got {len(preview_records)}: "
        f"{[r.message for r in preview_records]}"
    )


def test_non_utf8_config_is_logged_not_raised(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """A config file with a non-UTF-8 byte must not crash the sync command.

    ``tomllib.load`` decodes the file itself, so a stray byte raises
    ``UnicodeDecodeError`` -- a ``ValueError`` sibling of
    ``TOMLDecodeError``, and caught by neither it nor ``OSError``. The
    narrowed handler therefore let it escape, turning "your config is
    unreadable" into a traceback out of a governance command whose docstring
    two lines above promises a logged fallback to defaults.
    """
    (tmp_path / ".sigantry.toml").write_bytes(b'tenant_id = "\xff\xfe not utf-8"\n')
    monkeypatch.chdir(tmp_path)
    sync_cli_mod._PREVIEW_WARNING_EMITTED.clear()

    with caplog.at_level(logging.WARNING, logger="sigantry_core.sync.cli"):
        sync_cli_mod._emit_preview_warning_once()

    load_failures = [
        r.message for r in caplog.records if "Could not load Sigantry settings" in r.message
    ]
    assert load_failures, (
        f"the unreadable config was not reported at all: {[r.message for r in caplog.records]}"
    )
    assert "UnicodeDecodeError" in load_failures[0]


# ---------------------------------------------------------------------------
# sigantry 1.0.0 compatibility (1.0.1)
# ---------------------------------------------------------------------------

_UNPREFIXED_NAMES = frozenset({"PREVIEW_APIS_ACKNOWLEDGED", "TENANT_ID", "PROVIDER", "GATE"})


@pytest.fixture
def settings_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """No settings variable from the host; the working directory is ``tmp_path``."""
    for name in list(os.environ):
        upper = name.upper()
        if upper.startswith(("FDT_", "SIGANTRY_")) or upper in _UNPREFIXED_NAMES:
            monkeypatch.delenv(name)
    monkeypatch.chdir(tmp_path)
    sync_cli_mod._PREVIEW_WARNING_EMITTED.clear()
    yield tmp_path
    sync_cli_mod._PREVIEW_WARNING_EMITTED.clear()


def _preview_records(caplog: pytest.LogCaptureFixture) -> list[str]:
    return [
        r.message
        for r in caplog.records
        if r.name == "sigantry_core.sync.cli"
        and r.levelno == logging.WARNING
        and "preview" in r.message.lower()
    ]


def _load_failures(caplog: pytest.LogCaptureFixture) -> list[str]:
    return [r.message for r in caplog.records if "Could not load Sigantry settings" in r.message]


_BROKEN_CONFIGS = {
    "malformed": b"[auth\n",
    "invalid-value": b"[core]\ntenant_id = 5\n",
    "non-utf8": b'[core]\ntenant_id = "\xff\xfe not utf-8"\n',
}


@pytest.mark.parametrize("filename", [_LEGACY_CONFIG_FILENAME, _CONFIG_FILENAME])
@pytest.mark.parametrize("broken", sorted(_BROKEN_CONFIGS))
def test_env_acknowledgement_survives_a_config_that_fails_to_load(
    broken: str,
    filename: str,
    settings_env: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """1.0.0 fell back to an env-reading ``ToolkitSettings()``, so an exported
    acknowledgement kept the advisory quiet while the file was broken."""
    (settings_env / filename).write_bytes(_BROKEN_CONFIGS[broken])
    monkeypatch.setenv("FDT_WORKFLOW__PREVIEW_APIS_ACKNOWLEDGED", "true")

    with caplog.at_level(logging.WARNING, logger="sigantry_core.sync.cli"):
        sync_cli_mod._emit_preview_warning_once()

    assert _load_failures(caplog), [r.message for r in caplog.records]
    assert _preview_records(caplog) == []


def test_new_prefix_acknowledgement_survives_a_config_that_fails_to_load(
    settings_env: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    (settings_env / _CONFIG_FILENAME).write_bytes(_BROKEN_CONFIGS["malformed"])
    monkeypatch.setenv("SIGANTRY_WORKFLOW__PREVIEW_APIS_ACKNOWLEDGED", "true")

    with caplog.at_level(logging.WARNING, logger="sigantry_core.sync.cli"):
        sync_cli_mod._emit_preview_warning_once()

    assert _preview_records(caplog) == []


def test_malformed_config_without_env_still_warns(
    settings_env: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Control: without an acknowledgement anywhere the advisory still fires."""
    (settings_env / _LEGACY_CONFIG_FILENAME).write_bytes(_BROKEN_CONFIGS["malformed"])

    with caplog.at_level(logging.WARNING, logger="sigantry_core.sync.cli"):
        sync_cli_mod._emit_preview_warning_once()

    assert _load_failures(caplog)
    assert len(_preview_records(caplog)) == 1


def test_legacy_config_under_warnings_as_errors_keeps_the_acknowledgement(
    settings_env: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """``PYTHONWARNINGS=error``: 1.0.0 raised no warning here, so nothing may raise."""
    (settings_env / _LEGACY_CONFIG_FILENAME).write_text(
        "[workflow]\npreview_apis_acknowledged = true\n", encoding="utf-8"
    )
    monkeypatch.setenv("FDT_CORE__TENANT_ID", "t1")
    monkeypatch.setenv("TENANT_ID", "unprefixed")

    with warnings.catch_warnings():
        warnings.simplefilter("error")
        with caplog.at_level(logging.WARNING, logger="sigantry_core.sync.cli"):
            sync_cli_mod._emit_preview_warning_once()

    assert _preview_records(caplog) == []


def test_env_fallback_under_warnings_as_errors(
    settings_env: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    (settings_env / _LEGACY_CONFIG_FILENAME).write_bytes(_BROKEN_CONFIGS["malformed"])
    monkeypatch.setenv("FDT_WORKFLOW__PREVIEW_APIS_ACKNOWLEDGED", "true")

    with warnings.catch_warnings():
        warnings.simplefilter("error")
        with caplog.at_level(logging.WARNING, logger="sigantry_core.sync.cli"):
            sync_cli_mod._emit_preview_warning_once()

    assert _preview_records(caplog) == []


def test_sync_apply_prints_no_settings_warning(
    settings_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The commands record and drop settings warnings: 1.0.0 printed none, and
    a pipeline step that fails on any stderr output must keep passing.

    pytest records warnings instead of printing them, which would make this
    test pass whatever the command did, so the block prints them to stderr
    the way Python does outside pytest.
    """
    (settings_env / _LEGACY_CONFIG_FILENAME).write_text(
        "[workflow]\npreview_apis_acknowledged = true\n", encoding="utf-8"
    )
    (settings_env / _CONFIG_FILENAME).write_text("[core]\n", encoding="utf-8")
    monkeypatch.setenv("FDT_CORE__TENANT_ID", "t1")
    monkeypatch.setenv("SIGANTRY_CORE__TENANT_ID", "t2")
    monkeypatch.setenv("TENANT_ID", "unprefixed")
    _stub_apply_to_raise_validation_error(monkeypatch)
    manifest = settings_env / "synthetic-sync.yml"
    manifest.write_text("schema_version: '1.0.0'\nitems: []\n", encoding="utf-8")

    def print_to_stderr(
        message: Warning | str,
        category: type[Warning],
        filename: str,
        lineno: int,
        file: TextIO | None = None,
        line: str | None = None,
    ) -> None:
        sys.stderr.write(warnings.formatwarning(message, category, filename, lineno, line))

    with warnings.catch_warnings():
        warnings.simplefilter("always")
        warnings.showwarning = print_to_stderr
        result = runner.invoke(
            sync_app, ["apply", "--manifest", str(manifest), "--workspace-id", "ws-synthetic"]
        )

    assert result.exit_code == 1, result.output
    for marker in ("Warning:", "deprecated", "TENANT_ID", "sigantry: warning"):
        assert marker not in result.stderr, result.stderr


def test_unprefixed_acknowledgement_still_silences_the_advisory(
    settings_env: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """1.0.0 read ``PREVIEW_APIS_ACKNOWLEDGED`` unprefixed. Settings no longer
    do, but this one only quiets an advisory, so the sync commands honour it."""
    monkeypatch.setenv("PREVIEW_APIS_ACKNOWLEDGED", "true")

    with caplog.at_level(logging.WARNING, logger="sigantry_core.sync.cli"):
        sync_cli_mod._emit_preview_warning_once()

    assert _preview_records(caplog) == []


def test_unprefixed_acknowledgement_loses_to_a_file_value(
    settings_env: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Guard: 1.0.0 ranked an unprefixed name below the file."""
    (settings_env / _CONFIG_FILENAME).write_text(
        "[workflow]\npreview_apis_acknowledged = false\n", encoding="utf-8"
    )
    monkeypatch.setenv("PREVIEW_APIS_ACKNOWLEDGED", "true")

    with caplog.at_level(logging.WARNING, logger="sigantry_core.sync.cli"):
        sync_cli_mod._emit_preview_warning_once()

    assert len(_preview_records(caplog)) == 1
