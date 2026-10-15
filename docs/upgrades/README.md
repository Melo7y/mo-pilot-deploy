# 升级与回退：统一操作流程

以后升级从这里开始。固定顺序是 **确认版本 → 准备产物 → 备份 → 更新 → 验收**；每次版本说明只填写参数、兼容性和特殊操作，不再复制整套命令。新版本说明使用 [模板](version-template.md)。

| 本次改动 | 使用流程 | 服务影响 |
| --- | --- | --- |
| 只有前端，且兼容现有 API | A：前端更新 | 先构建，替换 web 时网页短暂中断 |
| 涉及后端、数据库或整套版本 | B：整套升级 | 停 web/api/backup，备份后迁移并启动 |

版本说明：[v0.1 → v0.2.0-rc.1](ui-acceptance-20261014.md) · [甘特图与需求人列](ui-patch-20261015.md)。这些是已记录的验收版本，不代表正式发布。

## A. 仅前端更新

确认版本说明写明兼容现有后端，准备 **镜像标签** 和 **完整前端 commit SHA**。在现有部署目录执行，替换最后一行的两个参数：

```bash
git fetch origin main &&
git show origin/main:scripts/update-web.sh > /tmp/mopilot-update-web.sh &&
bash /tmp/mopilot-update-web.sh 镜像标签 完整前端SHA
```

脚本从同级 `../frontend` 仓库获取源码，默认 fetch `main`，实际构建始终固定到指定 SHA，不切换分支、不混入本地改动。未合并的验收源码可设置 `MOPILOT_SOURCE_REF=分支或标签`；仓库位置不同时设置 `MOPILOT_FRONTEND_REPO=/实际路径`。构建代理沿用导出的 `HTTP_PROXY`、`HTTPS_PROXY`、`NO_PROXY`。

若现有部署使用额外 Compose 文件或项目名，将它们追加到命令末尾，例如 `-f compose.dingtalk.yaml -p mopilot`，后续检查与回退也沿用相同参数。脚本不会自动推断这些参数。

脚本先构建或复用来源一致的镜像；成功后将 `.env`、`compose.yaml` 和旧镜像名备份至 `~/mopilot-upgrade-backups/`，设置 `MOPILOT_WEB_IMAGE_TAG`，仅替换 `web` 并等待健康检查。构建失败不会停服务；来源不一致的同名镜像不会被覆盖；不执行迁移。

看到「更新完成」后强制刷新浏览器，验收本次界面。若失败，检查报错和 web 日志；需要回退时，在 `.env` 将 `MOPILOT_WEB_IMAGE_TAG` 设置为脚本打印的原前端标签，再执行：

```bash
docker compose --env-file .env -f compose.yaml up -d --no-deps --no-build --pull never --wait --wait-timeout 120 web
```

## B. 前后端整套升级

### 1. 准备版本与升级包

从版本说明取得 `TAG`、目标迁移版本和四文件升级包：

- `mopilot-images-版本.tar.gz`
- `mopilot-deploy-版本.tar.gz`
- `source.txt`
- `SHA256SUMS`

可以在测试服务器打包，也可以从构建机上传；`PACKAGE` 填实际存放四个文件的目录。统一打包命令是 `bash scripts/package.sh 镜像标签 平台 前端SHA 后端SHA 部署SHA 目标迁移版本`，三个提交需已在本机仓库中存在；默认输出到三个仓库父目录的 `.scratch/packages/镜像标签/`。`source.txt` 记录固定源码、平台和目标迁移。镜像使用新标签，不覆盖历史版本；生产只使用经过验证的发布产物。详细交付约定见 [README 第 4 节](../../README.md#4-离线镜像交付公网服务器不构建)。

### 2. 备份、更新、检查

进入**现有部署目录**，修改下方最前面的三个参数后执行完整代码块。如果原启动使用额外 `-f` 或 `-p`，将它们加入 `dc()`；保留 `.env` 的项目名、持久化路径与凭据。升级包会更新部署配置，自定义 Nginx 或 Compose 修改应先与新版本配置核对。

```bash
bash <<'UPGRADE' || echo '升级已中止，请处理上方错误；若已停机，保持维护状态直到修复或回退。'
set -euo pipefail
TAG=填写目标版本
PACKAGE=/填写四文件升级包所在目录
EXPECTED_MIGRATION=填写目标迁移版本
dc() { docker compose --env-file .env -f compose.yaml "$@" </dev/null; }

# 先验证文件及版本；失败时尚未停机。
test -f .env && test -f compose.yaml
for file in SHA256SUMS source.txt "mopilot-images-$TAG.tar.gz" "mopilot-deploy-$TAG.tar.gz"; do
  test -f "$PACKAGE/$file" || { echo "缺少文件：$PACKAGE/$file" >&2; exit 1; }
done
(cd "$PACKAGE" && sha256sum -c SHA256SUMS)
grep -Fx "version=$TAG" "$PACKAGE/source.txt"
grep -Fx "required_migration=$EXPECTED_MIGRATION" "$PACKAGE/source.txt"
dc config --quiet
gzip -dc "$PACKAGE/mopilot-images-$TAG.tar.gz" | docker load
docker image inspect "mopilot-api:$TAG" "mopilot-web:$TAG" >/dev/null

echo '备份配置，停机并备份数据库和附件'
umask 077
mkdir -p "$HOME/mopilot-upgrade-backups"
CONFIG_BACKUP="$(mktemp -d "$HOME/mopilot-upgrade-backups/full-XXXXXX")"
cp -R .env compose*.yaml nginx scripts "$CONFIG_BACKUP/"
echo "配置备份：$CONFIG_BACKUP"
dc stop web api backup
dc run --rm -T --pull never backup once
dc run --rm -T --pull never --entrypoint /bin/sh backup -ec '
  BACKUP="$(find /backups -maxdepth 1 -type d -name "backup-*" | sort | tail -n 1)"
  test -n "$BACKUP"
  echo "数据备份：$BACKUP"
  cd "$BACKUP"
  sha256sum -c SHA256SUMS
'

echo '更新配置，迁移并启动'
tar --exclude=.env --exclude=./.env -xzf "$PACKAGE/mopilot-deploy-$TAG.tar.gz" -C .
awk '!/^[[:space:]]*(export[[:space:]]+)?MOPILOT_(WEB_IMAGE_TAG|IMAGE_TAG)[[:space:]]*=/' .env > "$CONFIG_BACKUP/new.env"
printf '\nMOPILOT_IMAGE_TAG=%s\nMOPILOT_WEB_IMAGE_TAG=\n' "$TAG" >> "$CONFIG_BACKUP/new.env"
cat "$CONFIG_BACKUP/new.env" > .env
export MOPILOT_IMAGE_TAG="$TAG" MOPILOT_WEB_IMAGE_TAG=
dc config --quiet
dc run --rm -T --pull never migrate up
dc up -d --no-build --pull never
dc ps
dc run --rm -T --pull never migrate status
dc exec -T web nginx -t
UPGRADE
```

`backup ready` 只表示备份完成；还需看到迁移、启动与检查输出。失败会返回登录 shell，不会因 `set -e` 退出 SSH。数据备份在容器内校验，不要求登录用户能读取 `/srv/mopilot/backups`。整套升级会清空前端独立标签，避免 web 留在旧验收版。

### 3. 验收与回退

确认 `api`、`web`、`db` 显示 `healthy`，`backup` 正在运行，迁移达到版本说明要求；强制刷新后检查登录、需求/任务和历史附件，再验收本次新增功能。保留旧镜像、配置备份和数据备份。不要执行 `seed`、`bootstrap-admin` 或 `down -v`。

只有版本说明明确确认数据库兼容旧应用，才可以恢复旧配置和旧镜像并启动。存在不兼容迁移时保持停机，按 [数据恢复流程](../../README.md#10-恢复) 恢复这次备份的数据库和附件，再恢复对应旧配置及镜像；不盲目执行 `migrate down`。

## 维护约定

常规步骤只改本文、`scripts/update-web.sh` 和 `scripts/package.sh`。每次版本说明只记录版本参数、适用起点、迁移及回退兼容性、特殊操作、验收点与实际验证结果。例外处理验证后若具有通用性，再补入本文；不再为每个日期新增升级脚本。历史版本和镜像标签保持不变。

修改流程后执行 `python3 -m unittest discover -s tests -v`。测试使用真实 Git 归档、补丁、配置文件与校验和，模拟 Docker 构建和生命周期，覆盖失败时不启动服务、重复执行、配置保留、迁移失败及文档命令顺序；不能替代目标平台的镜像构建和真实环境验收。
