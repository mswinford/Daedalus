#!/usr/bin/env bash
# Install dev dependencies on a fresh machine. Idempotent — safe to re-run;
# pip/npm are no-ops when everything is already installed.
#
#   ./scripts/setup.sh            # backend + registry + frontend
#   ./scripts/setup.sh copilot    # same, plus the optional Copilot SDK extra
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

need() {
  if ! command -v "$1" >/dev/null 2>&1; then
    echo "error: $1 not found — install it first ($2)" >&2
    exit 1
  fi
}
need python3 "https://python.org (>= 3.11)"
need node "https://nodejs.org"
need npm "comes with node"

if ! python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)'; then
  echo "error: Python >= 3.11 required (found $(python3 --version 2>&1))" >&2
  exit 1
fi

echo "→ Python venv + backend/registry deps"
python3 -m venv .venv
EXTRA=dev
if [ "${1:-}" = "copilot" ] || [ "${1:-}" = "--copilot" ]; then
  if grep -q '^copilot' pyproject.toml; then
    EXTRA="dev,copilot"
  else
    echo "note: this checkout has no [copilot] extra — installing [dev] only"
  fi
fi
.venv/bin/pip install --quiet -e ".[$EXTRA]"

echo "→ Frontend deps"
(cd frontend && npm install --no-fund --no-audit)

echo
echo "Done. Start the stack with: ./scripts/dev.sh"
