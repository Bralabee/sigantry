"""Safety probes for preflight pre-deployment validation.

Every probe answers one question and reports one of four statuses. ``PASS``,
``WARN`` and ``FAIL`` are verdicts: the probe checked what it is for.
``SKIP`` means the probe checked nothing because it lacked what it needed,
and its message starts with ``not checked:`` and says what was missing. A
``SKIP`` never reads as a pass: the engine fails a ``--strict`` run on it
and the CLI names it in the summary line.

The probes never print, log or return a bearer token.
"""

from __future__ import annotations

import graphlib
import json
import os
import time
from pathlib import Path
from typing import Any

from sigantry_core.preflight.models import ProbeResult, ProbeStatus

#: Environment variables that say an operator configured a credential on
#: purpose. When one is set and no token can be acquired, that is a FAIL
#: (the configured credential does not work), not a SKIP.
_EXPLICIT_CREDENTIAL_VARS: tuple[str, ...] = (
    "AZURE_CLIENT_ID",
    "AZURE_CLIENT_SECRET",
    "AZURE_CLIENT_CERTIFICATE_PATH",
    "AZURE_FEDERATED_TOKEN_FILE",
)

#: Fabric capacity states that cannot run a deploy (Fabric REST ``CapacityState``).
_CAPACITY_DOWN_STATES: frozenset[str] = frozenset(
    {"paused", "suspended", "inactive", "deleting", "deleted", "provisionfailed"}
)


def _elapsed_ms(start: float) -> float:
    return (time.perf_counter() - start) * 1000


def _same_guid(left: str, right: str) -> bool:
    """Compare two GUIDs as Entra and Fabric do: case does not matter."""
    return left.strip().casefold() == right.strip().casefold()


class BaseProbe:
    """Base class for all preflight safety probes.

    ``run`` receives everything the CLI can attach: the manifest and
    parameters paths, the target environment label, a Fabric REST client, the
    token provider behind it, the target workspace id and the expected tenant
    id. A probe that lacks what it needs returns ``SKIP`` with a
    ``not checked:`` message; it never guesses.
    """

    name: str = "base"

    def run(
        self,
        *,
        manifest_path: Path,
        environment: str,
        params_path: Path | None = None,
        client: Any = None,
        token_provider: Any = None,
        workspace_id: str | None = None,
        tenant_id: str | None = None,
    ) -> ProbeResult:
        """Execute the probe and return a ProbeResult."""
        raise NotImplementedError


class SchemaSyntaxProbe(BaseProbe):
    """Validates the manifest, every item path it names, and ``parameters.yml``.

    The manifest must load as a ``sync.yml`` (``SyncManifest``, extras
    forbidden) or as a ``workspace.yml`` (the bootstrap JSON schema). A file
    that is neither fails with both loaders' first error. For a sync
    manifest every item's ``local_path`` must exist, and every ``.platform``
    or ``.ipynb`` file under it must parse. When ``--params`` is given the
    file is run through the same validator as ``sigantry config validate``.
    """

    name = "schema_syntax"

    def run(
        self,
        *,
        manifest_path: Path,
        environment: str,
        params_path: Path | None = None,
        client: Any = None,
        token_provider: Any = None,
        workspace_id: str | None = None,
        tenant_id: str | None = None,
    ) -> ProbeResult:
        start_time = time.perf_counter()

        if not manifest_path.exists():
            return ProbeResult(
                name=self.name,
                status=ProbeStatus.FAIL,
                message=f"Manifest file does not exist: {manifest_path}",
                duration_ms=_elapsed_ms(start_time),
            )

        kind, items, load_errors = _load_manifest(manifest_path)
        if kind is None:
            return ProbeResult(
                name=self.name,
                status=ProbeStatus.FAIL,
                message=(
                    f"{manifest_path} is neither a sync.yml nor a workspace.yml: "
                    f"{load_errors['sync']}; {load_errors['workspace']}"
                ),
                details={"errors": load_errors},
                duration_ms=_elapsed_ms(start_time),
            )

        errors: list[str] = []
        artifacts_checked = 0
        root_dir = manifest_path.parent

        # Every item path a sync manifest names must exist, and the fabric-cicd
        # files under it must parse. The packagers write ``.platform``
        # themselves, so its absence is not an error; a corrupt one is.
        for display_name, local_path in items:
            item_dir = root_dir / local_path
            if not item_dir.exists():
                errors.append(f"item {display_name!r}: local_path {local_path} does not exist")
                continue
            for artifact in _artifact_files(item_dir):
                artifacts_checked += 1
                problem = _artifact_problem(artifact)
                if problem:
                    errors.append(problem)

        params_checked = False
        if params_path is not None:
            params_checked = True
            problem = _parameters_problem(params_path)
            if problem:
                errors.append(problem)

        details: dict[str, Any] = {
            "manifest_kind": kind,
            "items": len(items),
            "artifacts_checked": artifacts_checked,
            "params_checked": params_checked,
        }

        if errors:
            details["errors"] = errors
            return ProbeResult(
                name=self.name,
                status=ProbeStatus.FAIL,
                message=f"{len(errors)} problem(s) in the manifest, item tree or parameters: "
                + "; ".join(errors[:3])
                + (" ..." if len(errors) > 3 else ""),
                details=details,
                duration_ms=_elapsed_ms(start_time),
            )

        parts = [f"{kind} manifest valid"]
        if kind == "sync":
            parts.append(f"{len(items)} item path(s) present")
            parts.append(f"{artifacts_checked} artifact file(s) parsed")
        else:
            parts.append("no item tree applies")
        parts.append("parameters.yml valid" if params_checked else "parameters.yml not given")
        return ProbeResult(
            name=self.name,
            status=ProbeStatus.PASS,
            message="; ".join(parts),
            details=details,
            duration_ms=_elapsed_ms(start_time),
        )


def _load_manifest(
    manifest_path: Path,
) -> tuple[str | None, list[tuple[str, str]], dict[str, str]]:
    """Return ``(kind, [(display_name, local_path)], errors)``.

    ``kind`` is ``"sync"`` or ``"workspace"``, or ``None`` when the file
    loads as neither, in which case ``errors`` holds each loader's first
    error under its key.
    """
    import yaml

    from sigantry_core.sync.errors import ManifestValidationError
    from sigantry_core.sync.manifest import load_manifest

    errors: dict[str, str] = {}
    try:
        manifest = load_manifest(manifest_path)
    except ManifestValidationError as exc:
        first = exc.violations[0] if getattr(exc, "violations", None) else None
        errors["sync"] = (
            f"sync.yml: {first['field']}: {first['reason']}" if first else f"sync.yml: {exc}"
        )
    except yaml.YAMLError as exc:
        errors["sync"] = f"sync.yml: YAML parse error: {exc}"
    except (OSError, ValueError) as exc:
        # A directory, an unreadable file, or bytes that are not UTF-8.
        errors["sync"] = f"sync.yml: {type(exc).__name__}: {exc}"
    else:
        return "sync", [(item.display_name, str(item.local_path)) for item in manifest.items], {}

    from sigantry_core.workspace.bootstrap import load_and_validate

    try:
        load_and_validate(manifest_path)
    except (OSError, ValueError, yaml.YAMLError) as exc:
        # BootstrapValidationError subclasses ValueError.
        errors["workspace"] = f"workspace.yml: {exc}"
    else:
        return "workspace", [], {}
    return None, [], errors


def _artifact_files(item_dir: Path) -> list[Path]:
    if item_dir.is_file():
        return [item_dir] if item_dir.name == ".platform" or item_dir.suffix == ".ipynb" else []
    found = list(item_dir.rglob(".platform"))
    found.extend(p for p in item_dir.rglob("*.ipynb") if "checkpoint" not in p.name)
    return sorted(found)


def _artifact_problem(artifact: Path) -> str | None:
    try:
        content = json.loads(artifact.read_text(encoding="utf-8"))
    except Exception as ex:
        return f"{artifact}: invalid JSON ({ex})"
    if not isinstance(content, dict):
        return f"{artifact}: content is not a JSON object"
    if artifact.suffix == ".ipynb" and "cells" not in content:
        return f"{artifact}: missing 'cells' key in notebook JSON"
    return None


def _parameters_problem(params_path: Path) -> str | None:
    """Run ``parameters.yml`` through the deploy validator; return its error."""
    import yaml

    from sigantry_core.deploy.parameters import load_and_validate

    try:
        load_and_validate(params_path)
    except (OSError, ValueError, KeyError, RuntimeError, yaml.YAMLError) as exc:
        # HardcodedGuidError subclasses ValueError; FileNotFoundError and a
        # directory given as the path are OSErrors; a parse error is a YAMLError.
        return f"parameters: {exc}"
    return None


class DependencyGraphProbe(BaseProbe):
    """Orders ``items[].depends_on`` and detects cycles.

    Not in the engine's default set. The shipped ``sync.yml`` schema
    (``SyncManifest``, extras forbidden) declares no item dependencies and
    ``workspace.yml`` has no items, so against a real manifest this probe
    ordered nothing and reported "0 items verified" as a pass. It stays
    importable for callers whose own manifests carry ``items[].depends_on``.
    """

    name = "dependency_graph"

    def run(
        self,
        *,
        manifest_path: Path,
        environment: str,
        params_path: Path | None = None,
        client: Any = None,
        token_provider: Any = None,
        workspace_id: str | None = None,
        tenant_id: str | None = None,
    ) -> ProbeResult:
        import yaml

        start_time = time.perf_counter()

        if not manifest_path.exists():
            return ProbeResult(
                name=self.name,
                status=ProbeStatus.SKIP,
                message="not checked: manifest not found",
                duration_ms=_elapsed_ms(start_time),
            )

        try:
            raw = yaml.safe_load(manifest_path.read_text(encoding="utf-8")) or {}
        except Exception as ex:
            return ProbeResult(
                name=self.name,
                status=ProbeStatus.FAIL,
                message=f"Failed to parse manifest YAML for dependency analysis: {ex}",
                duration_ms=_elapsed_ms(start_time),
            )

        items = raw.get("items", []) if isinstance(raw, dict) else []
        graph: dict[str, set[str]] = {}
        for item in items:
            if not isinstance(item, dict):
                continue
            name = item.get("name")
            if not name:
                continue
            graph[name] = set(item.get("depends_on", []) or [])

        if not graph:
            return ProbeResult(
                name=self.name,
                status=ProbeStatus.SKIP,
                message="not checked: the manifest declares no items[].depends_on; nothing to order",
                duration_ms=_elapsed_ms(start_time),
            )

        ts = graphlib.TopologicalSorter(graph)
        try:
            order = list(ts.static_order())
        except graphlib.CycleError as ex:
            return ProbeResult(
                name=self.name,
                status=ProbeStatus.FAIL,
                message=f"Circular dependency cycle detected: {ex.args[1]}",
                details={"cycle": ex.args[1]},
                duration_ms=_elapsed_ms(start_time),
            )
        return ProbeResult(
            name=self.name,
            status=ProbeStatus.PASS,
            message=f"DAG order verified: {len(order)} item(s) without circular dependencies",
            details={"execution_order": order},
            duration_ms=_elapsed_ms(start_time),
        )


class EntraScopeProbe(BaseProbe):
    """Acquires a Fabric token through the credential chain and reads its tenant.

    A token is acquired, never assumed: an environment variable that names a
    client id proves nothing. When ``tenant_id`` is given the token's ``tid``
    claim must match it. When no token can be acquired the result is a
    ``FAIL`` if the operator configured a credential (``AZURE_CLIENT_ID`` and
    friends are set) and a ``SKIP`` otherwise. The token itself is never
    returned, logged or printed.
    """

    name = "entra_scope"

    def run(
        self,
        *,
        manifest_path: Path,
        environment: str,
        params_path: Path | None = None,
        client: Any = None,
        token_provider: Any = None,
        workspace_id: str | None = None,
        tenant_id: str | None = None,
    ) -> ProbeResult:
        from sigantry_core.auth.audiences import FABRIC_SCOPE
        from sigantry_core.auth.diagnose import decode_token_claims
        from sigantry_core.auth.errors import TokenAcquisitionError

        start_time = time.perf_counter()

        if token_provider is None:
            return ProbeResult(
                name=self.name,
                status=ProbeStatus.SKIP,
                message="not checked: no token provider attached",
                duration_ms=_elapsed_ms(start_time),
            )

        configured = sorted(v for v in _EXPLICIT_CREDENTIAL_VARS if os.getenv(v))
        try:
            token = token_provider.get_token(FABRIC_SCOPE)
        except TokenAcquisitionError as exc:
            details: dict[str, Any] = {
                "credential": exc.credential_used,
                "configured_credential_vars": configured,
            }
            if configured:
                return ProbeResult(
                    name=self.name,
                    status=ProbeStatus.FAIL,
                    message=(
                        f"the configured credential ({', '.join(configured)}) could not "
                        f"acquire a Fabric token: {exc}"
                    ),
                    details=details,
                    duration_ms=_elapsed_ms(start_time),
                )
            return ProbeResult(
                name=self.name,
                status=ProbeStatus.SKIP,
                message=(
                    f"not checked: no credential in the chain could acquire a Fabric "
                    f"token ({exc}); sign in with az login, or set AZURE_CLIENT_ID with "
                    f"AZURE_CLIENT_SECRET or AZURE_FEDERATED_TOKEN_FILE"
                ),
                details=details,
                duration_ms=_elapsed_ms(start_time),
            )

        claims = decode_token_claims(token)
        token_tenant = claims.get("tid")
        credential = token_provider.last_credential_class(FABRIC_SCOPE)
        details = {
            "credential": credential,
            "tenant_id": token_tenant,
            "expected_tenant_id": tenant_id,
            "app_id": claims.get("appid"),
            "expires_on": claims.get("exp"),
        }

        if tenant_id is not None:
            if token_tenant is None:
                return ProbeResult(
                    name=self.name,
                    status=ProbeStatus.WARN,
                    message=(
                        f"Fabric token acquired via {credential}, but it carries no tid "
                        f"claim, so tenant {tenant_id} could not be confirmed"
                    ),
                    details=details,
                    duration_ms=_elapsed_ms(start_time),
                )
            if not _same_guid(str(token_tenant), tenant_id):
                return ProbeResult(
                    name=self.name,
                    status=ProbeStatus.FAIL,
                    message=(
                        f"Fabric token is for tenant {token_tenant}, not the expected "
                        f"{tenant_id}: the credential chain ({credential}) signed in elsewhere"
                    ),
                    details=details,
                    duration_ms=_elapsed_ms(start_time),
                )

        return ProbeResult(
            name=self.name,
            status=ProbeStatus.PASS,
            message=f"Fabric token acquired via {credential} for tenant {token_tenant}",
            details=details,
            duration_ms=_elapsed_ms(start_time),
        )


class CapacityStateProbe(BaseProbe):
    """Reads the target workspace's capacity and its state.

    Needs a Fabric client and the workspace id (``--workspace-id``): the
    target capacity is whichever one the workspace is assigned to. A
    workspace with no capacity, or one whose capacity is paused or otherwise
    down, fails. A capacity the principal cannot list is a ``WARN`` (assigned,
    state unknown). Without a client or a workspace id the probe is ``SKIP``.
    When no credential in the chain can acquire a token the probe is ``SKIP``
    as well, or ``FAIL`` when the operator configured one (the same rule as
    the Entra probe): it read nothing, so it reports nothing as checked.
    """

    name = "capacity_state"

    def run(
        self,
        *,
        manifest_path: Path,
        environment: str,
        params_path: Path | None = None,
        client: Any = None,
        token_provider: Any = None,
        workspace_id: str | None = None,
        tenant_id: str | None = None,
    ) -> ProbeResult:
        start_time = time.perf_counter()

        if workspace_id is None:
            return ProbeResult(
                name=self.name,
                status=ProbeStatus.SKIP,
                message="not checked: no --workspace-id, so there is no target capacity to read",
                duration_ms=_elapsed_ms(start_time),
            )
        if client is None:
            return ProbeResult(
                name=self.name,
                status=ProbeStatus.SKIP,
                message="not checked: no Fabric client attached",
                duration_ms=_elapsed_ms(start_time),
            )

        from sigantry_core.auth.errors import TokenAcquisitionError
        from sigantry_core.capacity.core import list_capacities
        from sigantry_core.workspace.core import get_workspace

        try:
            workspace = get_workspace(client, workspace_id)
        except TokenAcquisitionError as ex:
            configured = sorted(v for v in _EXPLICIT_CREDENTIAL_VARS if os.getenv(v))
            cred_details: dict[str, Any] = {
                "workspace_id": workspace_id,
                "credential": ex.credential_used,
                "configured_credential_vars": configured,
            }
            if configured:
                return ProbeResult(
                    name=self.name,
                    status=ProbeStatus.FAIL,
                    message=(
                        f"the configured credential ({', '.join(configured)}) could not "
                        f"acquire a Fabric token, so workspace {workspace_id} was not read: {ex}"
                    ),
                    details=cred_details,
                    duration_ms=_elapsed_ms(start_time),
                )
            return ProbeResult(
                name=self.name,
                status=ProbeStatus.SKIP,
                message=(
                    f"not checked: no credential in the chain could acquire a Fabric token, "
                    f"so workspace {workspace_id} was not read ({ex})"
                ),
                details=cred_details,
                duration_ms=_elapsed_ms(start_time),
            )
        except Exception as ex:
            return ProbeResult(
                name=self.name,
                status=ProbeStatus.FAIL,
                message=f"workspace {workspace_id} could not be read: {ex}",
                details={"workspace_id": workspace_id},
                duration_ms=_elapsed_ms(start_time),
            )

        details: dict[str, Any] = {
            "workspace_id": workspace_id,
            "workspace_name": workspace.display_name,
            "capacity_id": workspace.capacity_id,
        }
        if not workspace.capacity_id:
            return ProbeResult(
                name=self.name,
                status=ProbeStatus.FAIL,
                message=f"workspace {workspace.display_name!r} has no Fabric capacity assigned",
                details=details,
                duration_ms=_elapsed_ms(start_time),
            )

        try:
            capacity = next(
                (
                    c
                    for c in list_capacities(client)
                    if _same_guid(str(c.id), str(workspace.capacity_id))
                ),
                None,
            )
        except Exception as ex:
            return ProbeResult(
                name=self.name,
                status=ProbeStatus.WARN,
                message=(
                    f"capacity {workspace.capacity_id} is assigned, but its state could "
                    f"not be read: {ex}"
                ),
                details=details,
                duration_ms=_elapsed_ms(start_time),
            )
        if capacity is None:
            return ProbeResult(
                name=self.name,
                status=ProbeStatus.WARN,
                message=(
                    f"capacity {workspace.capacity_id} is assigned, but this principal "
                    f"cannot list it, so its state is unknown"
                ),
                details=details,
                duration_ms=_elapsed_ms(start_time),
            )

        details.update(
            {
                "capacity_name": capacity.display_name,
                "sku": capacity.sku_name,
                "state": capacity.state,
            }
        )
        state = (capacity.state or "").lower()
        label = f"capacity {capacity.display_name!r} ({capacity.sku_name or 'sku unknown'})"
        if state == "active":
            return ProbeResult(
                name=self.name,
                status=ProbeStatus.PASS,
                message=f"{label} is Active",
                details=details,
                duration_ms=_elapsed_ms(start_time),
            )
        if state in _CAPACITY_DOWN_STATES or not state:
            return ProbeResult(
                name=self.name,
                status=ProbeStatus.FAIL,
                message=f"{label} is {capacity.state or 'in an unknown state'}; a deploy cannot run on it",
                details=details,
                duration_ms=_elapsed_ms(start_time),
            )
        return ProbeResult(
            name=self.name,
            status=ProbeStatus.WARN,
            message=f"{label} is {capacity.state}; a deploy may block until it is Active",
            details=details,
            duration_ms=_elapsed_ms(start_time),
        )
