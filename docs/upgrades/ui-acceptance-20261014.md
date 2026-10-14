# UI 验收候选升级说明（2026-10-14）

本次由操作者执行服务器升级。候选版本为 v0.2.0-rc-ui-333a78a，包含右上角用户菜单、个人设置排版及 UI 验收清单的 9 项修复。本文不表示服务器已升级，也不表示 v0.2.0 已正式发布。

## 固定源码与已完成检查

| 项目 | 固定提交 |
| --- | --- |
| 前端 | 333a78aa10f9244f9c7c0be9b05028e229b61c2a |
| 后端 | 931fc13ff34c65391ea6c128457e43ce8007be44 |
| 部署运行配置基线 | a79ec993a357b8244104c0f93700667d69ca49b1 |

前端提交后 lint、230 项测试和生产构建通过，9 项 UI 问题已在桌面及 390px 手机宽度模拟中复验。本次 UI 修复没有新增后端代码或数据库迁移；对应的 v0.2.0 后端需要数据库迁移至 00011。旧版 v0.1.0 的迁移基线是 00008。

此前后端 931fc13 和部署配置的 linux/arm64 容器验收、08→11 升级恢复演练已有记录。本次新前端镜像的目标服务器、物理手机及真实 HTTPS 验收由操作者完成；不能用此前镜像检查代替本次部署验收。

选择升级路径：

- **仅更新 UI**：现有 API 已使用表中后端提交、迁移已到 00011，部署配置已兼容本基线。保持 API、数据库和现有集成配置，只更新 Web。
- **完整候选升级**：从 v0.1.0 升级，或无法确认现有后端与基线一致。按完整路径准备两个应用镜像、部署配置和数据库迁移。

本机现有三个仓库包含这些提交。以下构建通过 Git 归档固定源码，不使用工作区的临时文件；服务器使用离线镜像，不需要 Node.js 或 Go。遵循 [发布约定](../../CONTRIBUTING.md)，已交付的标签和镜像不要覆盖。

## 1. 在构建机准备镜像包

先在服务器执行下面命令，记录架构：

~~~bash
uname -m
~~~

x86_64 对应 linux/amd64，aarch64 对应 linux/arm64。以下命令在 **Bash** 中执行，构建机需要 Docker Buildx、Git、tar、gzip 和 shasum。按实际服务器修改 PLATFORM；若需要构建代理，使用 [部署 README](../../README.md) 的构建参数。

~~~bash
set -euo pipefail
cd /Users/melo/code/mo-pilot

TAG='v0.2.0-rc-ui-333a78a'
PLATFORM='linux/amd64' # 按上一步服务器架构修改
WEB_SHA='333a78aa10f9244f9c7c0be9b05028e229b61c2a'
API_SHA='931fc13ff34c65391ea6c128457e43ce8007be44'
DEPLOY_SHA='a79ec993a357b8244104c0f93700667d69ca49b1'

for repo in frontend backend mo-pilot-deploy; do
  test -z "$(git -C "$repo" status --porcelain)"
done
git -C frontend cat-file -e "$WEB_SHA^{commit}"
git -C backend cat-file -e "$API_SHA^{commit}"
git -C mo-pilot-deploy cat-file -e "$DEPLOY_SHA^{commit}"

# 已存在同名候选时停止；重建交付应选用新的 TAG。
for image in "mopilot-web:$TAG" "mopilot-api:$TAG"; do
  if docker image inspect "$image" >/dev/null 2>&1; then
    echo "镜像标签已存在，请使用新的 TAG：$image" >&2
    exit 1
  fi
done

BUILD_DIR="$(mktemp -d)"
trap 'rm -rf -- "$BUILD_DIR"' EXIT
OUTPUT_DIR="$PWD/.scratch/ui-upgrade-artifacts/$TAG"
mkdir -p "$BUILD_DIR/frontend" "$BUILD_DIR/backend" "$BUILD_DIR/deploy" "$OUTPUT_DIR"

git -C frontend archive "$WEB_SHA" | tar -x -C "$BUILD_DIR/frontend"
git -C backend archive "$API_SHA" | tar -x -C "$BUILD_DIR/backend"
git -C mo-pilot-deploy archive "$DEPLOY_SHA" | tar -x -C "$BUILD_DIR/deploy"

docker buildx build --platform "$PLATFORM" --load \
  --build-arg "COMMIT_HASH=$WEB_SHA" \
  --label "org.opencontainers.image.revision=$WEB_SHA" \
  --label "org.opencontainers.image.version=$TAG" \
  -t "mopilot-web:$TAG" "$BUILD_DIR/frontend"
docker buildx build --platform "$PLATFORM" --load \
  --label "org.opencontainers.image.revision=$API_SHA" \
  --label "org.opencontainers.image.version=$TAG" \
  -t "mopilot-api:$TAG" "$BUILD_DIR/backend"
docker pull --platform "$PLATFORM" postgres:17-alpine

docker image inspect --format \
  '{{.RepoTags}} {{.Os}}/{{.Architecture}} revision={{index .Config.Labels "org.opencontainers.image.revision"}}' \
  "mopilot-web:$TAG" "mopilot-api:$TAG" postgres:17-alpine

# 将本说明附入运行配置包；Git 归档不包含服务器 .env 或私密数据。
mkdir -p "$BUILD_DIR/deploy/docs/upgrades"
cp mo-pilot-deploy/docs/upgrades/ui-acceptance-20261014.md "$BUILD_DIR/deploy/docs/upgrades/"
docker save "mopilot-web:$TAG" "mopilot-api:$TAG" postgres:17-alpine \
  | gzip -1 > "$OUTPUT_DIR/mopilot-images-$TAG.tar.gz"
tar -czf "$OUTPUT_DIR/mopilot-deploy-$TAG.tar.gz" -C "$BUILD_DIR/deploy" .
cat > "$OUTPUT_DIR/source-$TAG.txt" <<EOF
candidate=$TAG
platform=$PLATFORM
frontend_commit=$WEB_SHA
backend_commit=$API_SHA
deploy_runtime_commit=$DEPLOY_SHA
required_migration=00011
EOF
(
  cd "$OUTPUT_DIR"
  shasum -a 256 "mopilot-images-$TAG.tar.gz" "mopilot-deploy-$TAG.tar.gz" \
    "source-$TAG.txt" > "SHA256SUMS-$TAG"
)
echo "交付目录：$OUTPUT_DIR"
~~~

核对镜像输出的架构、两个 revision 与上表完全一致。将交付目录的两个 tar.gz、source 文本和 SHA256SUMS 文件传到服务器。上面只生成候选产物，不创建正式产品标签或 GitHub Release。

## 2. 服务器预检与备份

在现有部署目录执行；示例是 /opt/mopilot/deploy。保持现有 Compose 项目名和数据目录，避免切换到另一套数据库卷。下面需要 Bash、Docker Compose v2、sha256sum、awk 和 curl。

~~~bash
set -euo pipefail
cd /opt/mopilot/deploy # 替换成现有部署目录
TAG='v0.2.0-rc-ui-333a78a'
UPLOAD_DIR="/opt/mopilot/packages/$TAG" # 替换成收到文件的目录
DATA_DIR='/srv/mopilot' # 与当前 .env 的 MOPILOT_DATA_DIR 一致
STATE_DIR="/opt/mopilot/upgrade-history/$TAG"

test -f .env
docker compose ls
read -rp '现有 Compose 项目名（上一步 NAME）: ' PROJECT_NAME
test -n "$PROJECT_NAME"
COMPOSE=(docker compose --project-name "$PROJECT_NAME" --env-file .env -f compose.yaml)

# 必须沿用现有钉钉启用状态，不能在升级时顺便启用或关闭。
read -rp '当前已启用 compose.dingtalk.yaml 吗（yes/no）: ' DINGTALK_MODE
case "$DINGTALK_MODE" in
  yes) COMPOSE+=(-f compose.dingtalk.yaml) ;;
  no) ;;
  *) echo '请输入 yes 或 no' >&2; exit 1 ;;
esac
~~~

下面继续在同一 Bash 终端执行；后续所有命令都使用同一 COMPOSE 数组：

~~~bash
"${COMPOSE[@]}" config --quiet
API_CID="$("${COMPOSE[@]}" ps -q api)"
WEB_CID="$("${COMPOSE[@]}" ps -q web)"
test -n "$API_CID"
test -n "$WEB_CID"
test -d "$DATA_DIR/attachments"
test -d "$DATA_DIR/backups"
test ! -e "$STATE_DIR"
install -d -m 0700 "$STATE_DIR/config"
cp .env compose.yaml "$STATE_DIR/config/"
if test -f compose.dingtalk.yaml; then
  cp compose.dingtalk.yaml "$STATE_DIR/config/"
fi
if test -f compose.ui-acceptance.yaml; then
  cp compose.ui-acceptance.yaml "$STATE_DIR/config/"
fi
chmod 0600 "$STATE_DIR/config/.env"
docker inspect --format '{{.Config.Image}}' "$API_CID" > "$STATE_DIR/old-api-image.txt"
docker inspect --format '{{.Config.Image}}' "$WEB_CID" > "$STATE_DIR/old-web-image.txt"
docker image inspect --format '{{index .Config.Labels "org.opencontainers.image.revision"}}' \
  "$(cat "$STATE_DIR/old-api-image.txt")"
"${COMPOSE[@]}" run --rm --pull never migrate status | tee "$STATE_DIR/migration-before.txt"

(
  cd "$UPLOAD_DIR"
  sha256sum -c "SHA256SUMS-$TAG"
)
gzip -dc "$UPLOAD_DIR/mopilot-images-$TAG.tar.gz" | docker load
docker image inspect --format '{{.RepoTags}} {{.Os}}/{{.Architecture}}' \
  "mopilot-web:$TAG" "mopilot-api:$TAG" postgres:17-alpine
~~~

如果原运行命令还使用了其他覆盖文件，把它们也加到 COMPOSE、备份到 STATE_DIR/config，并在升级后继续使用。不要展示展开后的 Compose 配置或 .env。

完整升级先进入维护窗口并停写；仅更新 UI 可继续运行 API：

~~~bash
# 仅完整升级执行这一行；UI-only 路径跳过。
"${COMPOSE[@]}" stop web api

"${COMPOSE[@]}" run --rm --pull never backup once
BACKUP_DIR="$(find "$DATA_DIR/backups" -maxdepth 1 -type d -name 'backup-*' | sort | tail -n 1)"
test -n "$BACKUP_DIR"
(cd "$BACKUP_DIR" && sha256sum -c SHA256SUMS)
printf '%s\n' "$BACKUP_DIR" > "$STATE_DIR/backup-path.txt"
~~~

备份或校验失败就停止升级。完整升级若已停写、尚未更新配置和迁移，可使用原 COMPOSE 的 up -d --no-build --pull never api web 恢复原服务，排查后重新安排维护窗口。

## 3A. 仅更新 UI

只在第 2 节确认现有 API 为 931fc13、迁移为 00011 且运行配置兼容时使用。不要修改 .env 的 MOPILOT_IMAGE_TAG，它还控制 API 和迁移镜像。

~~~bash
cat > compose.ui-acceptance.yaml <<EOF
services:
  web:
    image: mopilot-web:$TAG
EOF
"${COMPOSE[@]}" -f compose.ui-acceptance.yaml config --quiet
"${COMPOSE[@]}" -f compose.ui-acceptance.yaml up -d --no-deps --no-build --pull never web
"${COMPOSE[@]}" -f compose.ui-acceptance.yaml ps
~~~

以后所有会更新 Web 的 Compose 命令都要加 -f compose.ui-acceptance.yaml，防止 Web 回到 .env 中的旧镜像；同时保留原有其他覆盖文件。本路径不运行迁移，也不重建 API。

## 3B. 完整候选升级

先完成第 2 节停写、备份和校验。解压到临时目录后，只更新运行配置、模板和脚本，不覆盖 .env、证书、数据目录或自定义配置。此前已定制这些文件时先比较并合并改动。

~~~bash
PACKAGE_DIR="$(mktemp -d)"
tar -xzf "$UPLOAD_DIR/mopilot-deploy-$TAG.tar.gz" -C "$PACKAGE_DIR"
cp compose.yaml "$STATE_DIR/config/compose.yaml"
cp -R nginx scripts "$STATE_DIR/config/"
cp "$PACKAGE_DIR/compose.yaml" "$PACKAGE_DIR/compose.dingtalk.yaml" .
cp "$PACKAGE_DIR/nginx/default.conf.template" "$PACKAGE_DIR/nginx/http.conf.template" nginx/
cp "$PACKAGE_DIR/scripts/backup.sh" scripts/
rm -rf -- "$PACKAGE_DIR"

# 保持 .env 的其他值和权限，只替换明确的镜像版本。
umask 077
awk -v tag="$TAG" '
  BEGIN { found=0 }
  /^MOPILOT_IMAGE_TAG=/ { print "MOPILOT_IMAGE_TAG=" tag; found=1; next }
  { print }
  END { if (!found) print "MOPILOT_IMAGE_TAG=" tag }
' .env > .env.upgrade-tmp
chmod 0600 .env.upgrade-tmp
mv .env.upgrade-tmp .env

# 若沿用先前的 UI-only 覆盖文件，把 Web 镜像一同改成当前候选。
if test -f compose.ui-acceptance.yaml; then
  cat > compose.ui-acceptance.yaml <<EOF
services:
  web:
    image: mopilot-web:$TAG
EOF
  COMPOSE+=(-f compose.ui-acceptance.yaml)
fi
"${COMPOSE[@]}" config --quiet
"${COMPOSE[@]}" run --rm --pull never migrate up
"${COMPOSE[@]}" run --rm --pull never migrate status | tee "$STATE_DIR/migration-after.txt"
"${COMPOSE[@]}" up -d --no-build --pull never --remove-orphans
"${COMPOSE[@]}" ps
~~~

已有钉钉集成时，按 [README 钉钉配置](../../README.md#钉钉绑定与外部网络可选)核对 RobotCode、平台公开地址和原有回跳配置，保持同一企业与已登记地址。配置或迁移失败时停止继续启动，先定位原因；不要执行开发 seed、bootstrap-admin 或 migrate down。

## 4. 升级后验收

UI-only 路径的检查继续带其覆盖文件：

~~~bash
# 仅 UI-only 路径执行这一行，完整升级已在 3B 中处理。
COMPOSE+=(-f compose.ui-acceptance.yaml)

"${COMPOSE[@]}" ps
"${COMPOSE[@]}" exec -T web nginx -t
"${COMPOSE[@]}" exec -T api wget -qO- http://127.0.0.1:8080/readyz
read -rp '当前验收入口（协议和主机，含端口，不带末尾斜杠）: ' BASE_URL
curl -fsS "$BASE_URL/healthz"
test "$(curl -sS -o /dev/null -w '%{http_code}' "$BASE_URL/api/v1/tasks")" = 401
"${COMPOSE[@]}" logs --since 10m --tail 100 web api
docker inspect --format '{{.Config.Image}}' "$("${COMPOSE[@]}" ps -q web)"
~~~

Web 镜像应为 mopilot-web:v0.2.0-rc-ui-333a78a，API、Web 和数据库应健康，迁移为 00011。UI-only 路径另核对 API 镜像与 old-api-image.txt 一致。

浏览器硬刷新后，用已有账号验收：

1. 登录、需求/任务列表与详情正常，历史附件可下载；完整升级再用专门验收数据验证新建、流转与附件上传下载。
2. 用户按钮位于右上角，能看出可点击，个人设置的对齐、卡片间距、绑定状态和操作正常。
3. 工作台首屏可见排期；高级筛选可展开，清空、隐藏恢复正常。
4. 手机菜单选择需求/任务管理后自动收起；列表名称附近可直接看到状态和关键日期。
5. 手机周/月视图点击“今天”能定位可见日期，横滑后再次点击仍有效，原筛选条件保留。
6. 未修改任务取消直接关闭；实际修改后取消仍确认，“继续填写”保留内容。
7. 需求和任务详情的目录跳转不会被顶部栏挡住；筛选文字清楚、用户团队/角色筛选可用键盘操作。
8. 需求和任务附件只有一个“可选”标记。
9. 物理手机访问实际 HTTPS 入口，验证任务链接、登录返回和权限；真实钉钉通知另按既有验收计划操作。

记录实际镜像、迁移状态、入口和验收结果后，再按 [发布约定](../../CONTRIBUTING.md)整理正式清单、CHANGELOG、统一附注标签及交付记录。候选验收未完成时，不提前标记 v0.2.0 正式发布。

## 5. 回滚

**仅 UI 回退**：保留当前后端和数据库，通过最后一个覆盖文件明确切回备份记录的旧 Web 镜像：

~~~bash
OLD_WEB_IMAGE="$(cat "$STATE_DIR/old-web-image.txt")"
docker image inspect "$OLD_WEB_IMAGE" >/dev/null
cat > compose.rollback-web.yaml <<EOF
services:
  web:
    image: $OLD_WEB_IMAGE
EOF
"${COMPOSE[@]}" -f compose.rollback-web.yaml up -d --no-deps --no-build --pull never web
"${COMPOSE[@]}" -f compose.rollback-web.yaml exec -T web nginx -t
~~~

之后继续使用这个回滚覆盖文件，直到选定新的固定 Web 镜像；不要清理旧镜像或升级前备份。

**完整旧版本回退**：若涉及从 00008 升级、软删除或新绑定数据，不能只把应用镜像换回 v0.1.0。旧应用会重新暴露软删除需求及附件，已在升级演练中验证。优先前滚修复；必须回到旧版时，停写并按 [README 恢复流程](../../README.md#10-恢复)恢复第 2 节记录的数据库与附件备份，再恢复保存的运行配置及旧镜像。恢复会回到备份时点，升级后的写入不在旧备份中，应明确维护窗口和恢复范围。
