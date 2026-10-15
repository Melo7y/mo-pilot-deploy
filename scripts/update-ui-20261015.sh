#!/usr/bin/env bash
# Compatibility entry point. New versions use update-web.sh with explicit inputs.
set -euo pipefail
SCRIPT_DIR="$(cd -- "$(dirname -- "$0")" && pwd)"
UPDATER="$SCRIPT_DIR/update-web.sh"
if [ ! -f "$UPDATER" ]; then
  # Older instructions extracted this entry point to /tmp by itself.
  WORK="$(mktemp -d)"
  trap 'rm -rf "$WORK"' EXIT
  git fetch origin main </dev/null
  git show FETCH_HEAD:scripts/update-web.sh > "$WORK/update-web.sh"
  UPDATER="$WORK/update-web.sh"
fi
bash "$UPDATER" v0.2-ui-3c530b3 3c530b35f5ba891387aa41f2b3eb1384b5c35e6b "$@"
