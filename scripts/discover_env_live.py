"""Discover HS2_FABRIC_TEST_* candidate values from the live Fabric tenant.

Reads AZURE_* auth from ``.env.live`` (same loader as ``tests/integration/conftest.py``),
then calls the Fabric REST API via the toolkit's own client to enumerate:

  * capacities visible to the SP (``/v1/capacities``)
  * workspaces visible to the SP (``/v1/workspaces?roles=Admin,Contributor,Member``)
  * Environment items inside a chosen workspace (``/v1/workspaces/{id}/items?type=Environment``)

Prints a paste-ready block for the ``HS2_FABRIC_TEST_*`` section of ``.env.live``.
No secret is ever printed. No state is mutated.

Usage
-----
    conda activate sigantry-core
    python scripts/discover_env_live.py                 # matches workspace name containing 'aims'
    python scripts/discover_env_live.py --workspace-name AIMS-DEV
    python scripts/discover_env_live.py --list-only     # list only; do not pick a workspace
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path


def _load_env_file(path: Path) -> int:
    """Mirror of tests/integration/conftest.py::_load_env_file.

    Existing env vars always win so an exported override stays in effect.
    """
    if not path.is_file():
        return 0
    loaded = 0
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        if not key or not key.isidentifier():
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1]
        if key not in os.environ:
            os.environ[key] = value
            loaded += 1
    return loaded


def _print_stderr(msg: str = "") -> None:
    print(msg, file=sys.stderr)


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--workspace-name",
        default="aims",
        help="Case-insensitive substring for picking the workspace (default: 'aims').",
    )
    parser.add_argument(
        "--list-only",
        action="store_true",
        help="Only enumerate workspaces + capacities; do not attempt to pick a workspace.",
    )
    args = parser.parse_args()

    repo_root = Path(__file__).resolve().parents[1]
    env_live = repo_root / ".env.live"
    loaded = _load_env_file(env_live)
    _print_stderr(f"# loaded {loaded} vars from {env_live}")

    missing = [
        k
        for k in ("AZURE_TENANT_ID", "AZURE_CLIENT_ID", "AZURE_CLIENT_SECRET")
        if not os.environ.get(k)
    ]
    if missing:
        _print_stderr(f"ERROR: missing required env vars: {', '.join(missing)}")
        _print_stderr("       populate .env.live (see scripts/live-creds.template) and re-run")
        return 2

    # Imports deferred until after env is loaded so the token provider picks
    # up AZURE_* on construction.
    from sigantry_core.capacity.core import list_capacities
    from sigantry_core.client import FabricRestClient
    from sigantry_core.workspace.core import list_workspaces
    from sigantry_core.workspace.items import list_items

    tenant_id = os.environ["AZURE_TENANT_ID"]
    client = FabricRestClient.from_defaults(tenant_id=tenant_id)

    try:
        _print_stderr("\n# --- Capacities visible to the SP ---")
        capacities = list(list_capacities(client))
        if not capacities:
            _print_stderr("#   (none — SP has no Fabric Capacity reader/contributor role)")
        for cap in capacities:
            sku = f"{cap.sku_name or '-'}/{cap.sku_tier or '-'}"
            _print_stderr(
                f"#   {cap.id}  {cap.display_name!r}  sku={sku}  region={cap.region}  state={cap.state}"
            )

        _print_stderr("\n# --- Workspaces visible to the SP (Admin|Contributor|Member) ---")
        try:
            workspaces = list(list_workspaces(client, roles="Admin,Contributor,Member"))
        except Exception as exc:
            _print_stderr(
                f"#   list_workspaces(roles=...) failed ({exc!r}); retrying without role filter"
            )
            workspaces = list(list_workspaces(client))
        if not workspaces:
            _print_stderr("#   (none — the SP is not a member of any workspace; add it as Admin on")
            _print_stderr(
                "#    the target workspace in the Fabric portal: Workspace settings -> Manage access)"
            )
        for ws in workspaces:
            cap_id = ws.capacity_id or "-"
            _print_stderr(f"#   {ws.id}  {ws.display_name!r}  type={ws.type}  capacity={cap_id}")

        env_candidates: dict[str, str] = {"HS2_FABRIC_TEST_TENANT_ID": tenant_id}

        if not args.list_only:
            needle = args.workspace_name.lower()
            matches = [ws for ws in workspaces if needle in (ws.display_name or "").lower()]

            if not matches:
                _print_stderr(f"\n# WARNING: no workspace name contained {args.workspace_name!r}.")
                _print_stderr(
                    "#          pass --workspace-name <substring> with a value from the list above."
                )
            elif len(matches) > 1:
                _print_stderr(
                    f"\n# WARNING: {len(matches)} workspaces matched {args.workspace_name!r}; "
                    "refine with --workspace-name to disambiguate."
                )
                for ws in matches:
                    _print_stderr(f"#          {ws.id}  {ws.display_name!r}")
            else:
                ws = matches[0]
                env_candidates["HS2_FABRIC_TEST_WORKSPACE_ID"] = ws.id
                if ws.capacity_id:
                    env_candidates["HS2_FABRIC_TEST_CAPACITY_ID"] = ws.capacity_id

                _print_stderr(
                    f"\n# --- Environment items in workspace {ws.display_name!r} ({ws.id}) ---"
                )
                envs = list(list_items(client, ws.id, item_type="Environment"))
                if not envs:
                    _print_stderr(
                        "#   (no Environment items — leaving HS2_FABRIC_TEST_ENVIRONMENT_ID blank)"
                    )
                elif len(envs) == 1:
                    env_candidates["HS2_FABRIC_TEST_ENVIRONMENT_ID"] = envs[0].id
                    _print_stderr(f"#   {envs[0].id}  {envs[0].display_name!r}")
                else:
                    _print_stderr(
                        f"#   {len(envs)} Environment items — pick one and set ENVIRONMENT_ID manually:"
                    )
                    for it in envs:
                        _print_stderr(f"#     {it.id}  {it.display_name!r}")

            # If the workspace didn't expose a capacity and there's exactly one
            # visible capacity, it's almost certainly the right one — surface it.
            if "HS2_FABRIC_TEST_CAPACITY_ID" not in env_candidates and len(capacities) == 1:
                env_candidates["HS2_FABRIC_TEST_CAPACITY_ID"] = capacities[0].id

        _print_stderr("\n# --- paste-ready block for .env.live (stdout) ---")
        for k, v in env_candidates.items():
            print(f"{k}={v}")
        _print_stderr("")
        _print_stderr(
            "# Telemetry (HS2_FABRIC_TEST_DCE_URI / DCR_IMMUTABLE_ID) is not API-discoverable"
        )
        _print_stderr(
            "# here — it comes from the bicep/modules/dcr-telemetry.bicep outputs in your subscription."
        )
    finally:
        client.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
