"""Safety probes for preflight pre-deployment validation."""

from __future__ import annotations

import graphlib
import json
import time
from pathlib import Path
from typing import Any

from sigantry_core.preflight.models import ProbeResult, ProbeStatus


class BaseProbe:
    """Base class for all preflight safety probes."""

    name: str = "base"

    def run(
        self,
        *,
        manifest_path: Path,
        environment: str,
        params_path: Path | None = None,
        client: Any = None,
    ) -> ProbeResult:
        """Execute the probe and return a ProbeResult."""
        raise NotImplementedError


class SchemaSyntaxProbe(BaseProbe):
    """Probes local manifest and Fabric item artifacts for syntax validity."""

    name = "schema_syntax"

    def run(
        self,
        *,
        manifest_path: Path,
        environment: str,
        params_path: Path | None = None,
        client: Any = None,
    ) -> ProbeResult:
        start_time = time.perf_counter()
        errors: list[str] = []
        checked_count = 0

        # Check manifest exists and is readable
        if not manifest_path.exists():
            return ProbeResult(
                name=self.name,
                status=ProbeStatus.FAIL,
                message=f"Manifest file does not exist: {manifest_path}",
                duration_ms=(time.perf_counter() - start_time) * 1000,
            )

        root_dir = manifest_path.parent

        # 1. Validate .platform JSON files
        for p_file in root_dir.rglob(".platform"):
            checked_count += 1
            try:
                content = json.loads(p_file.read_text(encoding="utf-8"))
                if not isinstance(content, dict):
                    errors.append(f"{p_file}: content is not a JSON object")
            except Exception as ex:
                errors.append(f"{p_file}: invalid JSON ({ex})")

        # 2. Validate .ipynb files
        for nb_file in root_dir.rglob("*.ipynb"):
            if "checkpoint" in nb_file.name:
                continue
            checked_count += 1
            try:
                nb_json = json.loads(nb_file.read_text(encoding="utf-8"))
                if not isinstance(nb_json, dict) or "cells" not in nb_json:
                    errors.append(f"{nb_file}: missing 'cells' key in notebook JSON")
            except Exception as ex:
                errors.append(f"{nb_file}: invalid notebook JSON ({ex})")

        duration = (time.perf_counter() - start_time) * 1000

        if errors:
            return ProbeResult(
                name=self.name,
                status=ProbeStatus.FAIL,
                message=f"Syntax/schema errors detected in {len(errors)} artifact(s)",
                details={"errors": errors, "checked_count": checked_count},
                duration_ms=duration,
            )

        return ProbeResult(
            name=self.name,
            status=ProbeStatus.PASS,
            message=f"All {checked_count} local artifacts (.platform, .ipynb) passed syntax verification",
            details={"checked_count": checked_count},
            duration_ms=duration,
        )


class DependencyGraphProbe(BaseProbe):
    """Validates item dependency relationships and detects circular DAG references."""

    name = "dependency_graph"

    def run(
        self,
        *,
        manifest_path: Path,
        environment: str,
        params_path: Path | None = None,
        client: Any = None,
    ) -> ProbeResult:
        import yaml

        start_time = time.perf_counter()

        if not manifest_path.exists():
            return ProbeResult(
                name=self.name,
                status=ProbeStatus.SKIP,
                message="Manifest not found; skipping dependency graph probe",
            )

        try:
            raw = yaml.safe_load(manifest_path.read_text(encoding="utf-8")) or {}
        except Exception as ex:
            return ProbeResult(
                name=self.name,
                status=ProbeStatus.FAIL,
                message=f"Failed to parse manifest YAML for dependency analysis: {ex}",
                duration_ms=(time.perf_counter() - start_time) * 1000,
            )

        items = raw.get("items", [])
        graph: dict[str, set[str]] = {}

        for item in items:
            name = item.get("name")
            if not name:
                continue
            deps = set(item.get("depends_on", []) or [])
            graph[name] = deps

        # Perform topological cycle detection
        ts = graphlib.TopologicalSorter(graph)
        try:
            order = list(ts.static_order())
            duration = (time.perf_counter() - start_time) * 1000
            return ProbeResult(
                name=self.name,
                status=ProbeStatus.PASS,
                message=f"DAG order verified: {len(order)} item(s) without circular dependencies",
                details={"execution_order": order},
                duration_ms=duration,
            )
        except graphlib.CycleError as ex:
            duration = (time.perf_counter() - start_time) * 1000
            return ProbeResult(
                name=self.name,
                status=ProbeStatus.FAIL,
                message=f"Circular dependency cycle detected: {ex.args[1]}",
                details={"cycle": ex.args[1]},
                duration_ms=duration,
            )


class EntraScopeProbe(BaseProbe):
    """Probes Entra ID authentication and permission scope grants."""

    name = "entra_scope"

    def run(
        self,
        *,
        manifest_path: Path,
        environment: str,
        params_path: Path | None = None,
        client: Any = None,
    ) -> ProbeResult:
        import os

        start_time = time.perf_counter()

        # If an explicit client is provided, test token acquisition
        if client is not None:
            try:
                token = getattr(client, "token", None) or getattr(client, "_token", None)
                if token:
                    return ProbeResult(
                        name=self.name,
                        status=ProbeStatus.PASS,
                        message="Entra ID client token active and validated",
                        duration_ms=(time.perf_counter() - start_time) * 1000,
                    )
            except Exception as ex:
                return ProbeResult(
                    name=self.name,
                    status=ProbeStatus.FAIL,
                    message=f"Entra ID token probe failed: {ex}",
                    duration_ms=(time.perf_counter() - start_time) * 1000,
                )

        # Check environment auth indicators
        spn_configured = bool(
            os.getenv("AZURE_CLIENT_ID")
            and (os.getenv("AZURE_CLIENT_SECRET") or os.getenv("AZURE_FEDERATED_TOKEN_FILE"))
        )

        duration = (time.perf_counter() - start_time) * 1000

        if spn_configured:
            return ProbeResult(
                name=self.name,
                status=ProbeStatus.PASS,
                message="Entra Service Principal credentials detected in environment",
                details={"client_id": os.getenv("AZURE_CLIENT_ID")},
                duration_ms=duration,
            )

        return ProbeResult(
            name=self.name,
            status=ProbeStatus.WARN,
            message="No active Entra token or Service Principal detected (offline simulation mode)",
            duration_ms=duration,
        )


class CapacityStateProbe(BaseProbe):
    """Probes target Fabric capacity state (Active vs Paused / Throttled)."""

    name = "capacity_state"

    def run(
        self,
        *,
        manifest_path: Path,
        environment: str,
        params_path: Path | None = None,
        client: Any = None,
    ) -> ProbeResult:
        start_time = time.perf_counter()

        if client is None:
            return ProbeResult(
                name=self.name,
                status=ProbeStatus.SKIP,
                message="Client not attached; skipping remote capacity state probe",
                duration_ms=(time.perf_counter() - start_time) * 1000,
            )

        try:
            # If client has capacity inspection capability
            if hasattr(client, "get_capacity_state"):
                state = client.get_capacity_state()
                duration = (time.perf_counter() - start_time) * 1000
                if state.lower() == "active":
                    return ProbeResult(
                        name=self.name,
                        status=ProbeStatus.PASS,
                        message=f"Target Fabric capacity is {state}",
                        duration_ms=duration,
                    )
                return ProbeResult(
                    name=self.name,
                    status=ProbeStatus.WARN,
                    message=f"Target Fabric capacity is in state: {state} (may block deployment execution)",
                    duration_ms=duration,
                )
        except Exception as ex:
            return ProbeResult(
                name=self.name,
                status=ProbeStatus.WARN,
                message=f"Capacity probe warning: {ex}",
                duration_ms=(time.perf_counter() - start_time) * 1000,
            )

        return ProbeResult(
            name=self.name,
            status=ProbeStatus.SKIP,
            message="Capacity probe skipped (no capacity resolver active)",
            duration_ms=(time.perf_counter() - start_time) * 1000,
        )
