#!/usr/bin/env bash
# Run from the existing deployment directory. Only replace the web service.
set -euo pipefail

if [ "$#" -lt 2 ]; then
  echo 'Usage: bash update-web.sh IMAGE_TAG FRONTEND_COMMIT [-f extra-compose.yaml] [-p project]' >&2
  exit 64
fi
TAG="$1"
WEB_SHA="$2"
shift 2
[[ "$TAG" =~ ^[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}$ ]] || { echo '镜像标签格式不正确。' >&2; exit 64; }
[[ "$WEB_SHA" =~ ^[0-9a-f]{40}$ ]] || { echo '请使用完整的 40 位前端 commit SHA。' >&2; exit 64; }
COMPOSE_ARGS=()
while [ "$#" -gt 0 ]; do
  case "$1" in
    -f|--file|-p|--project-name)
      [ "$#" -ge 2 ] && [ -n "$2" ] || { echo 'Compose 参数缺少值。' >&2; exit 64; }
      COMPOSE_ARGS+=("$1" "$2")
      shift 2
      ;;
    *) echo "不支持的参数：$1" >&2; exit 64 ;;
  esac
done
FRONTEND_REPO="${MOPILOT_FRONTEND_REPO:-../frontend}"
SOURCE_REF="${MOPILOT_SOURCE_REF:-main}"
IMAGE="mopilot-web:$TAG"
dc() { docker compose --env-file .env -f compose.yaml ${COMPOSE_ARGS[@]+"${COMPOSE_ARGS[@]}"} "$@" </dev/null; }

test -f .env && test -f compose.yaml || {
  echo '请在现有 mo-pilot-deploy 部署目录运行。' >&2
  exit 1
}
git -C "$FRONTEND_REPO" rev-parse --git-dir >/dev/null
dc config --quiet
WEB_ID="$(dc ps -q web)"
test -n "$WEB_ID" || { echo '现有 web 未运行，请先检查部署目录和项目名。' >&2; exit 1; }
OLD_IMAGE="$(docker inspect --format '{{.Config.Image}}' "$WEB_ID")"

umask 077
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
# Compatibility patch for deployments predating the optional web image tag.
# Keep this self-contained so the script can also be extracted to /tmp.
cat > "$WORK/compose.patch" <<'PATCH'
diff --git a/compose.yaml b/compose.yaml
--- a/compose.yaml
+++ b/compose.yaml
@@ -103,9 +103,9 @@
   web:
-    image: mopilot-web:${MOPILOT_IMAGE_TAG:-local}
+    image: mopilot-web:${MOPILOT_WEB_IMAGE_TAG:-${MOPILOT_IMAGE_TAG:-local}}
     build:
       context: ${FRONTEND_CONTEXT:-../frontend}
       args:
-        COMMIT_HASH: ${MOPILOT_IMAGE_TAG:-production}
+        COMMIT_HASH: ${MOPILOT_WEB_IMAGE_TAG:-${MOPILOT_IMAGE_TAG:-production}}
     environment:
       MOPILOT_DOMAIN: ${MOPILOT_DOMAIN:?MOPILOT_DOMAIN is required}
       TZ: ${TZ:-Asia/Shanghai}
PATCH
PATCH_NEEDED=1
if git apply --reverse --check "$WORK/compose.patch" 2>/dev/null; then
  PATCH_NEEDED=0
else
  git apply --check "$WORK/compose.patch"
fi

echo '1/3 获取固定版本，构建前端（现有服务继续运行）'
git -C "$FRONTEND_REPO" fetch origin "$SOURCE_REF" </dev/null
git -C "$FRONTEND_REPO" cat-file -e "$WEB_SHA^{commit}"
if docker image inspect "$IMAGE" >/dev/null 2>&1; then
  BUILT_SHA="$(docker image inspect --format '{{index .Config.Labels "org.opencontainers.image.revision"}}' "$IMAGE")"
  test "$BUILT_SHA" = "$WEB_SHA" || {
    echo "已有 $IMAGE 的来源不匹配，停止，未覆盖镜像。" >&2
    exit 1
  }
  echo "复用已构建镜像 $IMAGE"
else
  git -C "$FRONTEND_REPO" archive "$WEB_SHA" > "$WORK/frontend.tar"
  docker build --build-arg "COMMIT_HASH=$WEB_SHA" \
    --build-arg HTTP_PROXY --build-arg HTTPS_PROXY --build-arg NO_PROXY \
    --label "org.opencontainers.image.revision=$WEB_SHA" \
    -t "$IMAGE" - < "$WORK/frontend.tar"
fi

echo '2/3 备份配置，仅更新前端镜像标签'
mkdir -p "$HOME/mopilot-upgrade-backups"
BACKUP="$(mktemp -d "$HOME/mopilot-upgrade-backups/web-XXXXXX")"
cp .env compose.yaml "$BACKUP/"
printf '%s\n' "$OLD_IMAGE" > "$BACKUP/previous-web-image.txt"
echo "配置备份：$BACKUP"
if [ "$PATCH_NEEDED" = 1 ]; then
  git apply "$WORK/compose.patch"
fi
awk '!/^[[:space:]]*(export[[:space:]]+)?MOPILOT_WEB_IMAGE_TAG[[:space:]]*=/' .env > "$WORK/new.env"
printf '\nMOPILOT_WEB_IMAGE_TAG=%s\n' "$TAG" >> "$WORK/new.env"
cat "$WORK/new.env" > .env
export MOPILOT_WEB_IMAGE_TAG="$TAG"
dc config --quiet
dc config --images | grep -Fx "$IMAGE" >/dev/null || {
  echo 'Compose 中的 web 镜像未采用指定标签，请检查额外覆盖文件；尚未替换容器。' >&2
  exit 1
}

echo '3/3 替换前端容器，等待健康检查（网页会短暂中断）'
if ! dc up -d --no-deps --no-build --pull never --wait --wait-timeout 120 web; then
  echo "前端未通过启动检查，请查看 docker compose --env-file .env -f compose.yaml logs --tail 50 web。旧镜像：${OLD_IMAGE}；配置备份：${BACKUP}" >&2
  exit 1
fi
dc ps web
echo "更新完成：${IMAGE}；请强制刷新浏览器后验收。"
echo "原前端镜像：${OLD_IMAGE}；后端和数据库保持原版本。"
