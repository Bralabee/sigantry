#!/usr/bin/env bash
# publish-v3-alpha.sh -- Sigantry v3.0 alpha-release publish script.
#
# Builds the five Python distributions that make up a Sigantry v3.0 alpha
# release and uploads them to an Azure DevOps Artifacts feed (or any twine
# repository URL passed via $ADO_FEED_URL). Used by Plan 10-08 (Wave 7) to
# cut v3.0.0-alpha.1 and any subsequent alpha rehearsal tags.
#
# Threat mitigations:
#   T-10-08-01 (Spoofing / Credential disclosure in logs): the `twine upload`
#     line is wrapped in `set +x` ... `set -x` so a `set -x` trace never
#     prints the resolved $TWINE_PASSWORD value to CI logs. Every other
#     command is traced (set -x is on by default after env-var validation).
#
# Required environment variables (the script exits non-zero if any are
# missing on a non-dry-run invocation):
#   ALPHA_VERSION   -- alpha version string, e.g. "3.0.0-alpha.1"
#                      (consumed informationally; the actual built version
#                      is whatever the package's pyproject.toml resolves to)
#   ADO_FEED_URL    -- twine repository URL for the ADO Artifacts feed
#                      (or any PEP 503 / legacy upload endpoint)
#   ADO_FEED_TOKEN  -- ADO Personal Access Token with package-write scope
#                      (or set TWINE_PASSWORD directly; the script forwards
#                      ADO_FEED_TOKEN -> TWINE_PASSWORD if TWINE_PASSWORD
#                      is unset)
#
# Usage:
#   ALPHA_VERSION=3.0.0-alpha.1 \
#   ADO_FEED_URL=https://pkgs.dev.azure.com/<org>/_packaging/<feed>/pypi/upload/ \
#   ADO_FEED_TOKEN=<pat> \
#     bash scripts/release/publish-v3-alpha.sh
#
#   bash scripts/release/publish-v3-alpha.sh --dry-run     # build only, no upload
#
# Exit codes:
#   0 -- all wheels built (and uploaded if not --dry-run); SHA256 summary printed.
#   1 -- missing required env var, build failure, or upload failure.

set -euo pipefail

# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------
DRY_RUN="false"
for arg in "$@"; do
    case "$arg" in
        --dry-run)
            DRY_RUN="true"
            ;;
        --help|-h)
            sed -n '2,40p' "$0"
            exit 0
            ;;
        *)
            echo "publish-v3-alpha.sh: unknown argument '$arg' (use --dry-run or --help)" >&2
            exit 1
            ;;
    esac
done

# ---------------------------------------------------------------------------
# Required environment variables
# ---------------------------------------------------------------------------
: "${ALPHA_VERSION:?ALPHA_VERSION must be set (e.g. 3.0.0-alpha.1)}"

if [[ "$DRY_RUN" == "false" ]]; then
    : "${ADO_FEED_URL:?ADO_FEED_URL must be set on a non-dry-run invocation}"
    # Forward ADO_FEED_TOKEN -> TWINE_PASSWORD if the latter is unset.
    if [[ -z "${TWINE_PASSWORD:-}" ]]; then
        : "${ADO_FEED_TOKEN:?ADO_FEED_TOKEN (or TWINE_PASSWORD) must be set on a non-dry-run invocation}"
        export TWINE_PASSWORD="$ADO_FEED_TOKEN"
    fi
    # ADO Artifacts twine username is conventionally any non-empty string;
    # default to __token__ if the caller has not chosen one.
    export TWINE_USERNAME="${TWINE_USERNAME:-__token__}"
fi

set -x

REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$REPO_ROOT"

# ---------------------------------------------------------------------------
# Build inputs -- the FIVE Sigantry v3.0 alpha distributions.
# Order matters only for log readability; each `python -m build` invocation
# is independent.
#
# 1. sigantry            -- repo root pyproject.toml (was fabric-dataops-toolkits)
# 2. sigantry-hs2        -- ./sigantry-hs2 (was fabric-dataops-toolkits-hs2)
# 3. fabric-dataops-toolkits     -- ./shim/fabric-dataops-toolkits (deprecation shim)
# 4. fabric-dataops-toolkits-hs2 -- ./shim/fabric-dataops-toolkits-hs2 (deprecation shim)
#
# A fifth artefact -- the PowerShell `Fabric` shim under ./shim/Fabric/ --
# is a PowerShell module, NOT a Python wheel, so it is intentionally NOT
# built here. The PowerShell publish path is handled separately on the
# tag-driven ADO pipeline (Plan 10-08 Task 3 packaging step).
# ---------------------------------------------------------------------------
PACKAGE_DIRS=(
    "."
    "sigantry-hs2"
    "shim/fabric-dataops-toolkits"
    "shim/fabric-dataops-toolkits-hs2"
)

# Friendly label for logs / summary printout (parallel array to PACKAGE_DIRS).
PACKAGE_LABELS=(
    "sigantry"
    "sigantry-hs2"
    "fabric-dataops-toolkits (shim)"
    "fabric-dataops-toolkits-hs2 (shim)"
)

set +x
echo "============================================================"
echo "publish-v3-alpha.sh"
echo "  ALPHA_VERSION : $ALPHA_VERSION"
echo "  DRY_RUN       : $DRY_RUN"
echo "  REPO_ROOT     : $REPO_ROOT"
echo "  Packages      :"
for label in "${PACKAGE_LABELS[@]}"; do
    echo "    - $label"
done
echo "============================================================"
set -x

# Clean any prior dist/ artefacts in the repo root + plugin + shim dirs so
# the SHA256 summary at the end reflects ONLY the wheels built by this run.
for d in "${PACKAGE_DIRS[@]}"; do
    rm -rf "$REPO_ROOT/$d/dist"
done

# ---------------------------------------------------------------------------
# Build phase -- runs even on --dry-run.
# ---------------------------------------------------------------------------
for i in "${!PACKAGE_DIRS[@]}"; do
    pkg_dir="${PACKAGE_DIRS[$i]}"
    label="${PACKAGE_LABELS[$i]}"
    set +x
    echo "------------------------------------------------------------"
    echo "[$((i + 1))/${#PACKAGE_DIRS[@]}] Building $label"
    echo "    cwd=$REPO_ROOT/$pkg_dir"
    echo "------------------------------------------------------------"
    set -x
    (
        cd "$REPO_ROOT/$pkg_dir"
        python -m build
    )
done

# ---------------------------------------------------------------------------
# Upload phase -- skipped on --dry-run.
# T-10-08-01 mitigation: every `twine upload` invocation is wrapped in
# `set +x` ... `set -x` so the resolved $TWINE_PASSWORD value never appears
# in CI logs even if the runner has command-trace enabled by default.
# ---------------------------------------------------------------------------
if [[ "$DRY_RUN" == "true" ]]; then
    set +x
    echo "============================================================"
    echo "DRY-RUN: skipping twine upload phase."
    echo "============================================================"
else
    for i in "${!PACKAGE_DIRS[@]}"; do
        pkg_dir="${PACKAGE_DIRS[$i]}"
        label="${PACKAGE_LABELS[$i]}"
        set +x
        echo "------------------------------------------------------------"
        echo "[upload $((i + 1))/${#PACKAGE_DIRS[@]}] $label -> $ADO_FEED_URL"
        echo "------------------------------------------------------------"
        # T-10-08-01: the upload command runs WITHOUT set -x so the
        # resolved $TWINE_PASSWORD does not land in the CI log. The
        # surrounding set +x / set -x dance is the load-bearing mitigation.
        twine upload \
            --repository-url "$ADO_FEED_URL" \
            --non-interactive \
            "$REPO_ROOT/$pkg_dir/dist/"*
        set -x
    done
fi

# ---------------------------------------------------------------------------
# Summary -- always printed (build phase produces wheels even on dry-run).
# ---------------------------------------------------------------------------
set +x
echo "============================================================"
echo "Built wheels + sdists (SHA256):"
echo "============================================================"
for i in "${!PACKAGE_DIRS[@]}"; do
    pkg_dir="${PACKAGE_DIRS[$i]}"
    label="${PACKAGE_LABELS[$i]}"
    dist_dir="$REPO_ROOT/$pkg_dir/dist"
    echo "--- $label ($dist_dir) ---"
    if [[ -d "$dist_dir" ]]; then
        # `sha256sum` is GNU coreutils; available on every Linux runner
        # (Ubuntu / Debian / Fabric-cicd containers).
        for f in "$dist_dir"/*; do
            [[ -f "$f" ]] || continue
            sha256sum "$f"
        done
    else
        echo "    (no dist/ directory produced)"
    fi
done
echo "============================================================"
echo "publish-v3-alpha.sh: DONE (alpha=$ALPHA_VERSION dry_run=$DRY_RUN)"
echo "============================================================"
