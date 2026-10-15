#!/usr/bin/env bash
# Compatibility entry point; preserve the original v0.2 candidate and output path.
set -euo pipefail
if [ "$#" -ne 1 ]; then
  echo 'Usage: bash scripts/package-v0.2.sh linux/amd64|linux/arm64' >&2
  exit 64
fi
SCRIPT_DIR="$(cd -- "$(dirname -- "$0")" && pwd)"
SOURCE_ROOT="$(cd -- "$SCRIPT_DIR/../.." && pwd)"
bash "$SCRIPT_DIR/package.sh" v0.2.0-rc.1 "$1" \
  0646cdbb5eea25d44fb7fbd47b8798e50ba9259c \
  931fc13ff34c65391ea6c128457e43ce8007be44 \
  a79ec993a357b8244104c0f93700667d69ca49b1 \
  00011 "$SOURCE_ROOT/.scratch/v0.2-package/v0.2.0-rc.1"
