#!/usr/bin/env bash
# scripts/build-walkthrough.sh -- Phase 15 / DEMO-03 / CONTEXT D-08
#
# Determinism wrapper around `npm ci && npm run render` for the
# Sigantry walkthrough mp4. Pins Node via .nvmrc, uses npm ci (not
# npm install) so the lockfile resolves identically every run, and
# forwards explicit codec + jpeg-quality flags to keep the mp4 size
# near 20MB for an ~80-second walkthrough (RESEARCH §Pitfall 4).
#
# Usage:
#   scripts/build-walkthrough.sh
#
# Output:
#   scripts/remotion/out/walkthrough.mp4
#
# CI runs the same logic via .github/workflows/sigantry-demo-mp4.yml
# (RESEARCH §Pattern 4) -- operator runs this script locally to
# reproduce CI's render before pushing.
#
# Determinism guards:
#   - set -euo pipefail (fail loud on any sub-command error)
#   - .nvmrc pin (Node 22.22.2; matches /remotion-tutorial/ baseline)
#   - npm ci (NOT npm install -- the latter mutates package-lock.json)
#   - explicit --codec h264 + --jpeg-quality 80 (Pitfall 4 size budget)
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" >/dev/null 2>&1 && pwd)"
REPO_ROOT="$(cd -- "${SCRIPT_DIR}/.." >/dev/null 2>&1 && pwd)"
REMOTION_DIR="${REPO_ROOT}/scripts/remotion"

if [[ ! -d "${REMOTION_DIR}" ]]; then
  echo "build-walkthrough: scripts/remotion/ not found at ${REMOTION_DIR}" >&2
  exit 1
fi

if [[ ! -f "${REMOTION_DIR}/package-lock.json" ]]; then
  echo "build-walkthrough: package-lock.json missing -- npm ci would fail." >&2
  echo "build-walkthrough: regenerate via 'cd ${REMOTION_DIR} && npm install --package-lock-only'." >&2
  exit 1
fi

# Pin Node version via .nvmrc if nvm is available; otherwise warn.
# CI uses actions/setup-node@v4 with node-version-file: scripts/remotion/.nvmrc
# instead of nvm itself.
if command -v nvm >/dev/null 2>&1; then
  cd "${REMOTION_DIR}"
  # shellcheck disable=SC1090,SC1091
  nvm use
elif [[ -s "${HOME}/.nvm/nvm.sh" ]]; then
  # shellcheck disable=SC1090,SC1091
  \. "${HOME}/.nvm/nvm.sh"
  cd "${REMOTION_DIR}"
  nvm use
else
  required_version="$(cat "${REMOTION_DIR}/.nvmrc")"
  detected_version="$(node --version 2>/dev/null || echo 'missing')"
  echo "build-walkthrough: nvm not available; expecting Node ${required_version} on PATH" >&2
  echo "build-walkthrough: detected Node ${detected_version}" >&2
  if [[ "${detected_version}" == "missing" ]]; then
    echo "build-walkthrough: Node not on PATH -- install Node ${required_version} or use nvm" >&2
    exit 1
  fi
fi

cd "${REMOTION_DIR}"
echo "build-walkthrough: running npm ci (deterministic install from package-lock.json)"
npm ci

echo "build-walkthrough: rendering Walkthrough composition with --codec h264 --jpeg-quality 80"
npm run render -- Walkthrough out/walkthrough.mp4 --codec h264 --jpeg-quality 80

echo "build-walkthrough: produced ${REMOTION_DIR}/out/walkthrough.mp4"
ls -lh "${REMOTION_DIR}/out/walkthrough.mp4"
