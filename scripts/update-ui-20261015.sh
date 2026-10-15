#!/usr/bin/env bash
# Run from the existing deployment directory after upgrading to v0.2.0-rc.1.
set -euo pipefail

WEB_SHA=3c530b35f5ba891387aa41f2b3eb1384b5c35e6b
CONFIG_SHA=479565d8fea4081fc0cec739635c54cf15558dca
TAG=v0.2-ui-3c530b3
IMAGE="mopilot-web:$TAG"
dc() { docker compose --env-file .env -f compose.yaml "$@" </dev/null; }

test -f .env && test -f compose.yaml || {
  echo '请在现有 mo-pilot-deploy 部署目录运行。' >&2
  exit 1
}
git cat-file -e "$CONFIG_SHA^{commit}"
git -C ../frontend rev-parse --git-dir >/dev/null
WEB_ID="$(dc ps -q web)"
test -n "$WEB_ID" || { echo '现有 web 未运行，请先检查部署目录和项目名。' >&2; exit 1; }
OLD_IMAGE="$(docker inspect --format '{{.Config.Image}}' "$WEB_ID")"

umask 077
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
git show "$CONFIG_SHA" --format= -- compose.yaml > "$WORK/compose.patch"
PATCH_NEEDED=1
if git apply --reverse --check "$WORK/compose.patch" 2>/dev/null; then
  PATCH_NEEDED=0
else
  git apply --check "$WORK/compose.patch"
fi

echo '1/3 获取固定版本，构建前端（现有服务继续运行）'
git -C ../frontend fetch origin fix/gantt-calendar </dev/null
git -C ../frontend cat-file -e "$WEB_SHA^{commit}"
if docker image inspect "$IMAGE" >/dev/null 2>&1; then
  BUILT_SHA="$(docker image inspect --format '{{index .Config.Labels "org.opencontainers.image.revision"}}' "$IMAGE")"
  test "$BUILT_SHA" = "$WEB_SHA" || {
    echo "已有 $IMAGE 的来源不匹配，停止，未覆盖镜像。" >&2
    exit 1
  }
  echo "复用已构建镜像 $IMAGE"
else
  git -C ../frontend archive "$WEB_SHA" > "$WORK/frontend.tar"
  docker build --build-arg "COMMIT_HASH=$WEB_SHA" \
    --build-arg HTTP_PROXY --build-arg HTTPS_PROXY --build-arg NO_PROXY \
    --label "org.opencontainers.image.revision=$WEB_SHA" \
    -t "$IMAGE" - < "$WORK/frontend.tar"
fi

echo '2/3 备份配置，仅更新前端镜像标签'
mkdir -p "$HOME/mopilot-upgrade-backups"
BACKUP="$(mktemp -d "$HOME/mopilot-upgrade-backups/ui-20261015-XXXXXX")"
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

echo '3/3 替换前端容器，等待健康检查（网页会短暂中断）'
if ! dc up -d --no-deps --no-build --pull never --wait --wait-timeout 120 web; then
  echo "前端未通过启动检查，请查看 docker compose --env-file .env -f compose.yaml logs --tail 50 web。旧镜像：${OLD_IMAGE}；配置备份：${BACKUP}" >&2
  exit 1
fi
dc ps web
echo "更新完成：${IMAGE}；请强制刷新浏览器后验收。"
echo "原前端镜像：${OLD_IMAGE}；后端和数据库保持原版本。"
