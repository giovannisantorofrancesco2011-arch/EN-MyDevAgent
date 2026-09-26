#!/usr/bin/env bash
# MyDevAgent — one-command start (Linux/macOS), without activating the virtual environment.
# Usage, from your project folder:   /path/to/mydevagent/run.sh   [mydevagent options]
# If it says "permission denied":    bash /path/to/mydevagent/run.sh
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BIN="$HERE/.venv/bin/mydevagent"
if [[ ! -x "$BIN" ]]; then
  echo "MyDevAgent isn't installed yet: starting the installation (only once)."
  bash "$HERE/scripts/install.sh"
fi
exec "$BIN" "$@"
