"""Every command that gets an Azure token for Fabric is pinned to one tenant.

Two parts:

* The class test walks the whole ``sigantry`` command tree and the
  ``diagnose-auth`` app, and requires every leaf command to be classified
  below: a new command fails until someone decides whether it gets a token.
* For every command in ``TENANT_BOUND`` the credential at the authority (where
  ``DefaultAzureCredential`` is constructed) is replaced by a fake that issues
  a token for tenant B while the command is pinned to tenant A, once with
  ``--tenant-id`` and once with ``SIGANTRY_CORE__TENANT_ID``. The command must
  stop with its own exit code, no traceback, and a line naming both tenants.
  With a token for tenant A, the fake must have been asked for tenant A.

No test reaches the network: ``httpx``, ``requests`` and ``socket`` connects
raise :class:`_NetworkReached`, which is not an ``Exception`` and so passes
every ``except Exception`` on the way out. Every token is a fake JWT.
"""

from __future__ import annotations

import base64
import json
import socket
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx
import pytest
import requests
from azure.core.credentials import AccessToken, AccessTokenInfo
from typer.main import get_command
from typer.testing import CliRunner

from sigantry_core.auth.cli import app as diagnose_app
from sigantry_core.auth.token_provider import reset_token_provider
from sigantry_core.cli import app

TENANT_A = "00000000-0000-0000-0000-00000000000a"
TENANT_B = "00000000-0000-0000-0000-00000000000b"
NOT_A_GUID = "contoso.onmicrosoft.com"

WS = "00000000-0000-0000-0000-0000000000c1"
CAP = "00000000-0000-0000-0000-0000000000c2"
ITEM = "00000000-0000-0000-0000-0000000000c3"
ENV = "00000000-0000-0000-0000-0000000000c4"
LABEL = "00000000-0000-0000-0000-0000000000c5"
SUB = "00000000-0000-0000-0000-0000000000c6"
CONN = "00000000-0000-0000-0000-0000000000c7"
VL = "00000000-0000-0000-0000-0000000000c8"

DIAGNOSE = "diagnose-auth"

#: Commands that get an Azure token for Fabric, Power BI, ARM, Graph or
#: Purview on the operator's behalf. Each must take ``--tenant-id``.
TENANT_BOUND: frozenset[str] = frozenset(
    {
        "capacity list",
        "capacity pause",
        "capacity resume",
        "deploy run",
        "diff",
        "env reconcile",
        "env sync",
        "env sync-all",
        "fabric-item set-binding",
        "git commit",
        "git connect",
        "git connection",
        "git disconnect",
        "git init",
        "git status",
        "git update",
        "label-sync",
        "preflight",
        "rbac-audit",
        "sync apply",
        "sync pull",
        "sync snapshot",
        "tenant-settings export",
        "variable-library create",
        "variable-library delete",
        "variable-library get",
        "variable-library list",
        "variable-library update",
        "workspace assign-capacity",
        "workspace bootstrap",
        "workspace create",
        "workspace delete",
        "workspace get",
        "workspace list",
        "workspace list-items",
        DIAGNOSE,
    }
)

#: Commands that never get an Azure token (read from their code, not their
#: names): local files, the audit ledger, the plugin registry, a DQ plugin.
NO_AZURE_TOKEN: frozenset[str] = frozenset(
    {
        "config validate",  # parameters.yml validation
        "deploy validate",  # parameters.yml, dependency graph, pre-commit
        "doctor",  # plugin registry diagnostics
        "dq gate",  # runs a registered DQ gate plugin; sigantry gets no token
        "fabric-item copy",  # local item folder copy
        "release diff",  # audit ledger
        "release list",  # audit ledger
        "release show",  # audit ledger
        "release verify",  # audit ledger
    }
)

#: Commands that get an Azure token only for Azure DevOps (and Key Vault for a
#: ``kv://`` GitHub App key), never for a Fabric-side API. The Fabric tenant
#: pin is out of their scope; ``release record`` has its own
#: ``--ado-tenant-id``, which the authority pins as well.
AZURE_DEVOPS_ONLY: frozenset[str] = frozenset(
    {
        "pr-bot run",  # --provider ado: Azure DevOps REST via the default chain
        "release record",  # --provider ado: Azure DevOps work items
    }
)

#: Exit code each command already used for an operational / auth failure.
EXIT_CODE: dict[str, int] = {"diff": 2, DIAGNOSE: 3}


def _leaf_commands() -> dict[str, list[str]]:
    """Every leaf command and its option names.

    A group whose callback takes the parameters and that has no
    subcommands (``diff``, ``doctor``, ``label-sync``, ``preflight``,
    ``rbac-audit``) is a leaf.
    """
    leaves: dict[str, list[str]] = {}

    def walk(cmd: Any, path: list[str]) -> None:
        subcommands = getattr(cmd, "commands", None) or {}
        if subcommands:
            for name, sub in subcommands.items():
                walk(sub, [*path, name])
            return
        leaves[" ".join(path)] = [opt for p in cmd.params for opt in getattr(p, "opts", [])]

    walk(get_command(app), [])
    leaves[DIAGNOSE] = [
        opt for p in get_command(diagnose_app).params for opt in getattr(p, "opts", [])
    ]
    return leaves


def test_every_command_is_classified_once() -> None:
    leaves = _leaf_commands()
    assert len(leaves) > 40, leaves  # the walk reached the whole tree
    sets = {
        "TENANT_BOUND": TENANT_BOUND,
        "NO_AZURE_TOKEN": NO_AZURE_TOKEN,
        "AZURE_DEVOPS_ONLY": AZURE_DEVOPS_ONLY,
    }
    for name in leaves:
        homes = [label for label, members in sets.items() if name in members]
        assert len(homes) == 1, f"{name!r} is classified in {homes or 'no set'}"
    for label, members in sets.items():
        stale = sorted(members - set(leaves))
        assert not stale, f"{label} names commands that do not exist: {stale}"


def test_tenant_bound_commands_take_tenant_id_and_the_rest_do_not() -> None:
    leaves = _leaf_commands()
    for name, options in leaves.items():
        if name in TENANT_BOUND:
            assert "--tenant-id" in options, f"{name} has no --tenant-id"
        else:
            assert "--tenant-id" not in options, f"{name} takes --tenant-id but is not bound"


# ---- the authority fake and the network block --------------------------------


class _NetworkReached(BaseException):
    """Raised where a request would leave the process."""


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _fake_jwt(tid: str) -> str:
    header = _b64url(json.dumps({"typ": "JWT", "alg": "none"}).encode())
    payload = _b64url(json.dumps({"tid": tid, "aud": "https://example.invalid"}).encode())
    return f"{header}.{payload}.sig"


@dataclass
class _Authority:
    """Stands in for ``DefaultAzureCredential``; every credential it builds issues ``tid``."""

    tid: str = TENANT_B
    constructed: list[dict[str, Any]] = field(default_factory=list)
    requested_tenants: list[str | None] = field(default_factory=list)

    def build(self, **kwargs: Any) -> _FakeCredential:
        self.constructed.append(kwargs)
        return _FakeCredential(self)


class _FakeCredential:
    def __init__(self, authority: _Authority) -> None:
        self._authority = authority

    def get_token(self, *scopes: str, tenant_id: str | None = None, **_: Any) -> AccessToken:
        self._authority.requested_tenants.append(tenant_id)
        return AccessToken(_fake_jwt(self._authority.tid), int(time.time()) + 3600)

    def get_token_info(self, *scopes: str, options: Any = None) -> AccessTokenInfo:
        self._authority.requested_tenants.append((options or {}).get("tenant_id"))
        return AccessTokenInfo(_fake_jwt(self._authority.tid), int(time.time()) + 3600)

    def close(self) -> None:
        return None


def _blocked(*_args: Any, **_kwargs: Any) -> Any:
    raise _NetworkReached("a test request tried to leave the process")


@pytest.fixture
def authority(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[_Authority]:
    fake = _Authority()
    monkeypatch.setattr("sigantry_core.auth.token_provider.DefaultAzureCredential", fake.build)
    monkeypatch.setattr(httpx.Client, "send", _blocked)
    monkeypatch.setattr(requests.Session, "request", _blocked)
    monkeypatch.setattr(socket.socket, "connect", _blocked)
    monkeypatch.setattr(socket, "create_connection", _blocked)
    for name in (
        "SIGANTRY_CORE__TENANT_ID",
        "FDT_CORE__TENANT_ID",
        "fdt_core__tenant_id",
        "AZURE_TENANT_ID",
        "AZURE_CLIENT_ID",
        "AZURE_CLIENT_SECRET",
        "AZURE_CLIENT_CERTIFICATE_PATH",
        "AZURE_FEDERATED_TOKEN_FILE",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("SIGANTRY_WORKFLOW__PREVIEW_APIS_ACKNOWLEDGED", "true")
    work = tmp_path / "work"
    work.mkdir()
    monkeypatch.chdir(work)
    reset_token_provider()
    yield fake
    reset_token_provider()


# ---- minimal valid arguments per command ------------------------------------


def _sync_yml(tmp: Path) -> str:
    path = tmp / "sync.yml"
    path.write_text('schema_version: "1.0"\nitems: []\n', encoding="utf-8")
    return str(path)


def _wheel(tmp: Path) -> str:
    path = tmp / "pkg-1.0.0-py3-none-any.whl"
    path.write_bytes(b"not really a wheel")
    return str(path)


def _envs_yml(tmp: Path) -> str:
    path = tmp / "environments.yml"
    path.write_text(
        'schema_version: "1.0"\n'
        "targets:\n"
        "  - name: dev\n"
        f'    workspace_id: "{WS}"\n'
        f'    environment_id: "{ENV}"\n'
        f'    wheels: ["{_wheel(tmp)}"]\n',
        encoding="utf-8",
    )
    return str(path)


def _workspace_yml(tmp: Path) -> str:
    path = tmp / "workspace.yml"
    path.write_text(
        f'schema_version: "1.0"\nworkspace:\n  name: "ws-test"\n  capacity_id: "{CAP}"\n',
        encoding="utf-8",
    )
    return str(path)


def _items_tree(tmp: Path) -> str:
    tree = tmp / "items"
    tree.mkdir()
    (tree / "parameters.yml").write_text(
        "find_replace: []\nkey_value_replace: []\n", encoding="utf-8"
    )
    return str(tree)


_ARGS: dict[str, Callable[[Path], list[str]]] = {
    "capacity list": lambda t: [],
    "capacity pause": lambda t: [SUB, "rg-test", "cap-test", "--force", "--runbook-id", "INC-1"],
    "capacity resume": lambda t: [SUB, "rg-test", "cap-test", "--force", "--runbook-id", "INC-1"],
    "deploy run": lambda t: [
        "--source",
        _items_tree(t),
        "--workspace-id",
        WS,
        "--environment",
        "DEV",
        "--item-types",
        "Notebook",
    ],
    "diff": lambda t: ["--workspace-id", WS, "--manifest", _sync_yml(t)],
    "env reconcile": lambda t: [
        "--workspace-id",
        WS,
        "--environment-id",
        ENV,
        "--wheel",
        _wheel(t),
    ],
    "env sync": lambda t: ["--workspace-id", WS, "--environment-id", ENV, "--wheel", _wheel(t)],
    "env sync-all": lambda t: ["--manifest", _envs_yml(t)],
    "fabric-item set-binding": lambda t: [
        "--workspace-id",
        WS,
        "--item-id",
        ITEM,
        "--environment-id",
        ENV,
        "--environment-workspace-id",
        WS,
    ],
    "git commit": lambda t: ["--workspace-id", WS, "--workspace-head", "h1", "--comment", "c"],
    "git connect": lambda t: [
        "--workspace-id",
        WS,
        "--ado-organization",
        "org",
        "--ado-project",
        "proj",
        "--ado-repository",
        "repo",
        "--branch",
        "main",
        "--directory",
        "/",
        "--git-connection-id",
        CONN,
    ],
    "git connection": lambda t: ["--workspace-id", WS],
    "git disconnect": lambda t: ["--workspace-id", WS, "--force", "--runbook-id", "INC-1"],
    "git init": lambda t: ["--workspace-id", WS],
    "git status": lambda t: ["--workspace-id", WS],
    "git update": lambda t: [
        "--workspace-id",
        WS,
        "--workspace-head",
        "h1",
        "--remote-commit",
        "c1",
    ],
    "label-sync": lambda t: ["--workspace-id", WS, "--label-id", LABEL, "--sp"],
    "preflight": lambda t: ["--manifest", _sync_yml(t)],
    "rbac-audit": lambda t: [],
    "sync apply": lambda t: ["--manifest", _sync_yml(t), "--workspace-id", WS],
    "sync pull": lambda t: ["--workspace-id", WS, "--into", str(t / "pulled")],
    "sync snapshot": lambda t: ["--workspace-id", WS],
    "tenant-settings export": lambda t: [],
    "variable-library create": lambda t: ["--workspace-id", WS, "--name", "vl"],
    "variable-library delete": lambda t: [
        VL,
        "--workspace-id",
        WS,
        "--force",
        "--runbook-id",
        "INC-1",
    ],
    "variable-library get": lambda t: [VL, "--workspace-id", WS],
    "variable-library list": lambda t: ["--workspace-id", WS],
    "variable-library update": lambda t: [VL, "--workspace-id", WS, "--name", "vl2"],
    "workspace assign-capacity": lambda t: [WS, CAP],
    "workspace bootstrap": lambda t: [_workspace_yml(t)],
    "workspace create": lambda t: ["--name", "ws-test"],
    "workspace delete": lambda t: [WS, "--force", "--runbook-id", "INC-1"],
    "workspace get": lambda t: [WS],
    "workspace list": lambda t: [],
    "workspace list-items": lambda t: [WS],
    DIAGNOSE: lambda t: [],
}


def test_every_tenant_bound_command_has_test_arguments() -> None:
    assert set(_ARGS) == set(TENANT_BOUND)


@dataclass
class _Outcome:
    exit_code: int | None
    output: str
    exception: BaseException | None
    network_reached: bool


def _run(command: str, tmp: Path, *extra: str) -> _Outcome:
    if command == DIAGNOSE:
        target, argv = diagnose_app, [*_ARGS[command](tmp), *extra]
    else:
        target, argv = app, [*command.split(), *_ARGS[command](tmp), *extra]
    try:
        result = CliRunner().invoke(target, argv)
    except _NetworkReached:
        return _Outcome(None, "", None, network_reached=True)
    return _Outcome(result.exit_code, result.output, result.exception, network_reached=False)


def _assert_refused(outcome: _Outcome, command: str, *names: str) -> None:
    assert not outcome.network_reached, f"{command}: a request left after the token was issued"
    assert outcome.exit_code == EXIT_CODE.get(command, 1), outcome.output
    assert isinstance(outcome.exception, SystemExit), repr(outcome.exception)
    assert "Traceback" not in outcome.output
    lines = [line for line in outcome.output.splitlines() if all(n in line for n in names)]
    assert lines, f"{command}: no single line names {names}:\n{outcome.output}"


_BOUND = sorted(TENANT_BOUND)


@pytest.mark.parametrize("command", _BOUND)
def test_flag_pin_refuses_a_token_from_another_tenant(
    command: str, authority: _Authority, tmp_path: Path
) -> None:
    authority.tid = TENANT_B
    outcome = _run(command, tmp_path, "--tenant-id", TENANT_A)
    _assert_refused(outcome, command, TENANT_A, TENANT_B)
    assert authority.requested_tenants, f"{command}: no token was requested"
    assert all(t == TENANT_A for t in authority.requested_tenants)


@pytest.mark.parametrize("command", _BOUND)
def test_settings_pin_refuses_a_token_from_another_tenant(
    command: str, authority: _Authority, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SIGANTRY_CORE__TENANT_ID", TENANT_A)
    authority.tid = TENANT_B
    outcome = _run(command, tmp_path)
    _assert_refused(outcome, command, TENANT_A, TENANT_B)
    assert authority.requested_tenants, f"{command}: no token was requested"
    assert all(t == TENANT_A for t in authority.requested_tenants)


@pytest.mark.parametrize("command", _BOUND)
def test_a_pinned_command_requests_the_pinned_tenant(
    command: str, authority: _Authority, tmp_path: Path
) -> None:
    authority.tid = TENANT_A
    _run(command, tmp_path, "--tenant-id", TENANT_A.upper())
    assert authority.requested_tenants, f"{command}: no token was requested"
    assert all(t is not None and t.lower() == TENANT_A for t in authority.requested_tenants)


@pytest.mark.parametrize("command", _BOUND)
def test_a_tenant_that_is_not_a_guid_is_refused_before_any_credential(
    command: str, authority: _Authority, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "flag").mkdir()
    outcome = _run(command, tmp_path / "flag", "--tenant-id", NOT_A_GUID)
    _assert_refused(outcome, command, NOT_A_GUID, "GUID")

    (tmp_path / "settings").mkdir()
    monkeypatch.setenv("SIGANTRY_CORE__TENANT_ID", NOT_A_GUID)
    outcome = _run(command, tmp_path / "settings")
    _assert_refused(outcome, command, NOT_A_GUID, "GUID")
    assert authority.constructed == []
    assert authority.requested_tenants == []


@pytest.mark.parametrize("command", _BOUND)
def test_unreadable_settings_stop_a_command_without_tenant_id(
    command: str, authority: _Authority, tmp_path: Path
) -> None:
    Path(".sigantry.toml").write_text("[core\ntenant_id = \n", encoding="utf-8")
    outcome = _run(command, tmp_path)
    _assert_refused(outcome, command, "core.tenant_id", "--tenant-id")
    assert authority.requested_tenants == []


def test_the_flag_wins_over_the_settings(
    authority: _Authority, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SIGANTRY_CORE__TENANT_ID", TENANT_B)
    authority.tid = TENANT_A
    _run("workspace list", tmp_path, "--tenant-id", TENANT_A)
    assert authority.requested_tenants == [TENANT_A]


def test_no_flag_and_no_setting_requests_no_tenant(authority: _Authority, tmp_path: Path) -> None:
    authority.tid = TENANT_B
    outcome = _run("workspace list", tmp_path)
    assert outcome.network_reached
    assert authority.requested_tenants == [None]


def test_the_flag_does_not_load_the_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    from sigantry_core import _cli_tenant

    def _refuse() -> Any:
        raise AssertionError("settings were loaded although --tenant-id was given")

    monkeypatch.setattr(_cli_tenant, "load_settings_for_cli", _refuse)
    assert _cli_tenant.resolve_tenant_id(f" {TENANT_A} ") == TENANT_A


def test_a_blank_setting_means_no_pin(
    authority: _Authority, monkeypatch: pytest.MonkeyPatch
) -> None:
    from sigantry_core import _cli_tenant

    monkeypatch.setenv("SIGANTRY_CORE__TENANT_ID", "  ")
    assert _cli_tenant.resolve_tenant_id(None) is None


def test_preflight_entra_probe_fails_naming_both_tenants(
    authority: _Authority, tmp_path: Path
) -> None:
    authority.tid = TENANT_B
    outcome = _run("preflight", tmp_path, "--tenant-id", TENANT_A, "--json")
    assert outcome.exit_code == 1
    start = outcome.output.index("{")
    report = json.loads(outcome.output[start:])
    entra = next(r for r in report["results"] if r["name"] == "entra_scope")
    assert entra["status"] == "FAIL"
    assert TENANT_A in entra["message"] and TENANT_B in entra["message"]


def test_diagnose_auth_reports_the_mismatch_as_no_usable_token(
    authority: _Authority, tmp_path: Path
) -> None:
    authority.tid = TENANT_B
    outcome = _run(DIAGNOSE, tmp_path, "--tenant-id", TENANT_A, "--output", "json")
    assert outcome.exit_code == 3
    assert "WARNING" not in outcome.output
    start = outcome.output.index("{")
    payload = json.loads(outcome.output[start:])
    assert payload["exit_code"] == 3
    assert TENANT_A in payload["error"] and TENANT_B in payload["error"]


def test_rbac_audit_expands_groups_through_a_pinned_graph_client(
    authority: _Authority, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Group expansion reads Microsoft Graph on the operator's behalf: same pin."""
    from sigantry_core.governance import principal_expansion

    graph = principal_expansion._GraphClient
    real = graph.from_defaults.__func__  # type: ignore[attr-defined]
    seen: list[str | None] = []

    def _recording(cls: Any, *, tenant_id: str | None = None, **kwargs: Any) -> Any:
        seen.append(tenant_id)
        return real(cls, tenant_id=tenant_id, **kwargs)

    monkeypatch.setattr(graph, "from_defaults", classmethod(_recording))
    authority.tid = TENANT_A
    _run("rbac-audit", tmp_path, "--tenant-id", TENANT_A)
    assert seen == [TENANT_A]


def test_an_empty_flag_counts_as_not_given(
    authority: _Authority, monkeypatch: pytest.MonkeyPatch
) -> None:
    from sigantry_core import _cli_tenant

    monkeypatch.setenv("SIGANTRY_CORE__TENANT_ID", TENANT_A)
    assert _cli_tenant.resolve_tenant_id("") == TENANT_A
    assert _cli_tenant.resolve_tenant_id("  ") == TENANT_A


def test_release_record_refuses_an_ado_tenant_that_is_not_a_guid(
    authority: _Authority, tmp_path: Path
) -> None:
    """``--ado-tenant-id`` reaches the same pinned provider: a clean refusal, no traceback."""
    result = CliRunner().invoke(
        app,
        [
            "release",
            "record",
            "--provider",
            "ado",
            "--ado-organization",
            "org",
            "--ado-project",
            "proj",
            "--ado-tenant-id",
            NOT_A_GUID,
            "--release-id",
            "r1",
            "--workspace",
            WS,
            "--work-items",
            "1",
            "--approver",
            "someone@example.invalid",
            "--audit-dir",
            str(tmp_path / "audit"),
        ],
    )
    assert result.exit_code == 1, result.output
    assert isinstance(result.exception, SystemExit)
    assert any(NOT_A_GUID in line and "GUID" in line for line in result.output.splitlines())
    assert authority.constructed == []
    assert not (tmp_path / "audit").exists()


@pytest.mark.parametrize("command", _BOUND)
def test_help_lists_every_option_at_80_columns(
    command: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("COLUMNS", "80")
    leaves = _leaf_commands()
    if command == DIAGNOSE:
        result = CliRunner().invoke(diagnose_app, ["--help"])
    else:
        result = CliRunner().invoke(app, [*command.split(), "--help"])
    assert result.exit_code == 0, result.output
    for option in leaves[command]:
        if option.startswith("--") and option not in ("--install-completion", "--show-completion"):
            assert option in result.output, f"{command}: {option} missing from --help"


def test_preflight_capacity_probe_fails_on_a_refused_token(
    authority: _Authority, tmp_path: Path
) -> None:
    authority.tid = TENANT_B
    outcome = _run("preflight", tmp_path, "--tenant-id", TENANT_A, "--workspace-id", WS, "--json")
    assert outcome.exit_code == 1, outcome.output
    report = json.loads(outcome.output[outcome.output.index("{") :])
    capacity = next(r for r in report["results"] if r["name"] == "capacity_state")
    assert capacity["status"] == "FAIL"
    assert TENANT_A in capacity["message"] and TENANT_B in capacity["message"]


@pytest.mark.parametrize("command", _BOUND)
def test_a_settings_error_names_the_field_but_never_prints_its_value(
    command: str, authority: _Authority, tmp_path: Path
) -> None:
    sentinel = "SETTING-VALUE-SENTINEL-4242"
    Path(".sigantry.toml").write_text(f'[release]\ngithub = "{sentinel}"\n', encoding="utf-8")
    outcome = _run(command, tmp_path)
    _assert_refused(outcome, command, "release.github", "--tenant-id")
    assert sentinel not in outcome.output
    assert authority.requested_tenants == []
