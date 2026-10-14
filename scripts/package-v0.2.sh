#!/usr/bin/env bash
# Build the fixed v0.2 acceptance sources and prepare an offline upgrade package.
set -euo pipefail

if [ "$#" -ne 1 ]; then
  echo "Usage: bash scripts/package-v0.2.sh linux/amd64|linux/arm64" >&2
  exit 64
fi
PLATFORM="$1"
case "$PLATFORM" in
  linux/amd64|linux/arm64) ;;
  *) echo "Unsupported platform: $PLATFORM" >&2; exit 64 ;;
esac

TAG='v0.2.0-rc.1'
WEB_SHA='0646cdbb5eea25d44fb7fbd47b8798e50ba9259c'
API_SHA='931fc13ff34c65391ea6c128457e43ce8007be44'
DEPLOY_SHA='a79ec993a357b8244104c0f93700667d69ca49b1'
SCRIPT_DIR="$(cd -- "$(dirname -- "$0")" && pwd)"
DEPLOY_DIR="$(cd -- "$SCRIPT_DIR/.." && pwd)"
SOURCE_ROOT="$(cd -- "$DEPLOY_DIR/.." && pwd)"
OUTPUT_DIR="$SOURCE_ROOT/.scratch/v0.2-package/$TAG"

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
  PROXY="ip:port"
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
      --build-arg HTTP_PROXY="$PROXY" \
      --build-arg HTTPS_PROXY="$PROXY" \
      "$@" -t "$image" "$context"
  fi
}

build_image "mopilot-web:$TAG" "$WEB_SHA" "$WORK_DIR/frontend" --build-arg "COMMIT_HASH=$WEB_SHA"
build_image "mopilot-api:$TAG" "$API_SHA" "$WORK_DIR/backend"
mkdir -p "$WORK_DIR/deploy/docs/upgrades"
cp "$DEPLOY_DIR/docs/upgrades/ui-acceptance-20261014.md" "$WORK_DIR/deploy/docs/upgrades/"
docker save "mopilot-web:$TAG" "mopilot-api:$TAG" \
  | gzip -1 > "$WORK_DIR/package/mopilot-images-$TAG.tar.gz"
tar -czf "$WORK_DIR/package/mopilot-deploy-$TAG.tar.gz" -C "$WORK_DIR/deploy" .
cat > "$WORK_DIR/package/source.txt" <<EOF
version=$TAG
platform=$PLATFORM
frontend_commit=$WEB_SHA
backend_commit=$API_SHA
deploy_runtime_commit=$DEPLOY_SHA
required_migration=00011
EOF
(
  cd "$WORK_DIR/package"
  "${CHECKSUM[@]}" "mopilot-images-$TAG.tar.gz" "mopilot-deploy-$TAG.tar.gz" source.txt > SHA256SUMS
)
mkdir -p "$(dirname -- "$OUTPUT_DIR")"
mv "$WORK_DIR/package" "$OUTPUT_DIR"
echo "Package ready: $OUTPUT_DIR"
