#!/usr/bin/env bash
# Build pinned sources and produce the standard four-file upgrade package.
set -euo pipefail

if [ "$#" -lt 6 ] || [ "$#" -gt 7 ]; then
  echo 'Usage: bash scripts/package.sh TAG PLATFORM WEB_SHA API_SHA DEPLOY_SHA MIGRATION [OUTPUT_DIR]' >&2
  exit 64
fi
TAG="$1"
PLATFORM="$2"
WEB_SHA="$3"
API_SHA="$4"
DEPLOY_SHA="$5"
MIGRATION="$6"
[[ "$TAG" =~ ^[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}$ ]] || exit 64
for revision in "$WEB_SHA" "$API_SHA" "$DEPLOY_SHA"; do
  [[ "$revision" =~ ^[0-9a-f]{40}$ ]] || { echo 'Use full commit SHAs.' >&2; exit 64; }
done
[[ "$MIGRATION" =~ ^[0-9]+$ ]] || { echo 'Migration must be a version number.' >&2; exit 64; }
case "${MOPILOT_INCLUDE_POSTGRES:-0}" in
  0|1) ;;
  *) echo 'MOPILOT_INCLUDE_POSTGRES must be 0 or 1.' >&2; exit 64 ;;
esac
case "$PLATFORM" in
  linux/amd64|linux/arm64) ;;
  *) echo "Unsupported platform: $PLATFORM" >&2; exit 64 ;;
esac

SCRIPT_DIR="$(cd -- "$(dirname -- "$0")" && pwd)"
DEPLOY_DIR="$(cd -- "$SCRIPT_DIR/.." && pwd)"
SOURCE_ROOT="$(cd -- "$DEPLOY_DIR/.." && pwd)"
OUTPUT_DIR="${7:-$SOURCE_ROOT/.scratch/packages/$TAG}"

for command in docker git tar gzip; do
  command -v "$command" >/dev/null
done
if command -v sha256sum >/dev/null; then
  CHECKSUM=(sha256sum)
else
  command -v shasum >/dev/null
  CHECKSUM=(shasum -a 256)
fi
if [ -e "$OUTPUT_DIR" ]; then
  echo "Package directory already exists: $OUTPUT_DIR" >&2
  exit 1
fi

WORK_DIR="$(mktemp -d)"
trap 'rm -rf -- "$WORK_DIR"' EXIT
mkdir -p "$WORK_DIR/frontend" "$WORK_DIR/backend" "$WORK_DIR/deploy" "$WORK_DIR/package"
git -C "$SOURCE_ROOT/frontend" archive "$WEB_SHA" | tar -x -C "$WORK_DIR/frontend"
git -C "$SOURCE_ROOT/backend" archive "$API_SHA" | tar -x -C "$WORK_DIR/backend"
git -C "$DEPLOY_DIR" archive "$DEPLOY_SHA" | tar -x -C "$WORK_DIR/deploy"

build_image() {
  local image="$1" revision="$2" context="$3"
  shift 3
  if docker image inspect "$image" >/dev/null 2>&1; then
    local metadata
    metadata="$(docker image inspect --format \
      '{{index .Config.Labels "org.opencontainers.image.revision"}} {{.Os}}/{{.Architecture}} {{index .Config.Labels "org.opencontainers.image.version"}}' "$image")"
    if [ "$metadata" != "$revision $PLATFORM $TAG" ]; then
      echo "Existing image does not match the fixed source/platform: $image" >&2
      exit 1
    fi
    echo "Using existing verified metadata: $image"
  else
    docker buildx build --platform "$PLATFORM" --load \
      --label "org.opencontainers.image.revision=$revision" \
      --label "org.opencontainers.image.version=$TAG" \
      --build-arg HTTP_PROXY --build-arg HTTPS_PROXY --build-arg NO_PROXY \
      "$@" -t "$image" "$context"
  fi
}

build_image "mopilot-web:$TAG" "$WEB_SHA" "$WORK_DIR/frontend" --build-arg "COMMIT_HASH=$WEB_SHA"
build_image "mopilot-api:$TAG" "$API_SHA" "$WORK_DIR/backend"
IMAGES=("mopilot-web:$TAG" "mopilot-api:$TAG")
# Existing deployments already have PostgreSQL. Include it for first-time offline installs only.
if [ "${MOPILOT_INCLUDE_POSTGRES:-0}" = 1 ]; then
  docker pull --platform "$PLATFORM" postgres:17-alpine
  IMAGES+=(postgres:17-alpine)
fi
mkdir -p "$WORK_DIR/deploy/docs/upgrades"
cp -R "$DEPLOY_DIR/docs/upgrades/." "$WORK_DIR/deploy/docs/upgrades/"
docker save "${IMAGES[@]}" \
  | gzip -1 > "$WORK_DIR/package/mopilot-images-$TAG.tar.gz"
tar -czf "$WORK_DIR/package/mopilot-deploy-$TAG.tar.gz" -C "$WORK_DIR/deploy" .
cat > "$WORK_DIR/package/source.txt" <<EOF
version=$TAG
platform=$PLATFORM
frontend_commit=$WEB_SHA
backend_commit=$API_SHA
deploy_runtime_commit=$DEPLOY_SHA
required_migration=$MIGRATION
includes_postgres=${MOPILOT_INCLUDE_POSTGRES:-0}
EOF
(
  cd "$WORK_DIR/package"
  "${CHECKSUM[@]}" "mopilot-images-$TAG.tar.gz" "mopilot-deploy-$TAG.tar.gz" source.txt > SHA256SUMS
)
mkdir -p "$(dirname -- "$OUTPUT_DIR")"
mv "$WORK_DIR/package" "$OUTPUT_DIR"
echo "Package ready: $OUTPUT_DIR"
