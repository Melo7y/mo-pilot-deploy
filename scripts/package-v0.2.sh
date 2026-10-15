#!/usr/bin/env bash
# Formal v0.2.0 release; the optional argument selects the target platform.
set -euo pipefail
if [ "$#" -gt 1 ]; then
  echo 'Usage: bash scripts/package-v0.2.sh [linux/amd64|linux/arm64]' >&2
  exit 64
fi
SCRIPT_DIR="$(cd -- "$(dirname -- "$0")" && pwd)"
bash "$SCRIPT_DIR/package.sh" v0.2.0 "$@"
