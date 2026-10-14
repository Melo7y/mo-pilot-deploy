# v0.1 → v0.2 停机升级

前后端一起更新，数据库从 00008 迁移到 00011。v0.2 验收镜像统一使用 **v0.2.0-rc.1**，包含 UI 修复和新 Logo。

## 1. 本机打包，上传服务器

在本机项目根目录执行：

~~~bash
bash mo-pilot-deploy/scripts/package-v0.2.sh linux/amd64
~~~

服务器若是 ARM，最后一个参数改为 linux/arm64。将生成的 **.scratch/v0.2-package/v0.2.0-rc.1/** 内四个文件上传到服务器 **/tmp/mopilot-v0.2/**。

## 2. 服务器停机并备份

以下命令在**同一个 Bash 终端**执行。部署目录、数据目录按现有环境修改；保持原 Compose 项目名。如果原启动命令有 -p 或额外的 -f，把它们原样加到下面的 dc 函数中。

~~~bash
set -euo pipefail
cd /opt/mopilot/deploy
TAG=v0.2.0-rc.1
PACKAGE=/tmp/mopilot-v0.2
DATA=/srv/mopilot
CONFIG_BACKUP="$DATA/backups/config-before-v0.2"
dc() { docker compose --env-file .env -f compose.yaml "$@"; }

(cd "$PACKAGE" && sha256sum -c SHA256SUMS)
umask 077
mkdir -m 0700 "$CONFIG_BACKUP"
cp -R .env compose*.yaml nginx scripts "$CONFIG_BACKUP/"
dc stop web api backup
dc run --rm --pull never backup once
BACKUP="$(find "$DATA/backups" -maxdepth 1 -type d -name 'backup-*' | sort | tail -n 1)"
(cd "$BACKUP" && sha256sum -c SHA256SUMS)
~~~

备份或校验失败就停在这里，不继续升级。

## 3. 导入前后端镜像，更新部署文件

~~~bash
gzip -dc "$PACKAGE/mopilot-images-$TAG.tar.gz" | docker load
tar -xzf "$PACKAGE/mopilot-deploy-$TAG.tar.gz" -C .
sed -i "s/^MOPILOT_IMAGE_TAG=.*/MOPILOT_IMAGE_TAG=$TAG/" .env
grep -qx "MOPILOT_IMAGE_TAG=$TAG" .env
dc config --quiet
~~~

原 .env、数据库卷、附件和证书保留。若有自定义 Nginx 配置，升级前将对应修改合入新模板。

## 4. 迁移数据库，启动前后端

~~~bash
dc run --rm --pull never migrate up
dc up -d --no-build --pull never --remove-orphans
~~~

不要执行 seed、bootstrap-admin 或 down -v。

## 5. 检查并验收

~~~bash
dc ps
dc run --rm --pull never migrate status
dc exec -T web nginx -t
~~~

确认 API、Web、数据库健康，迁移为 **00011**。浏览器硬刷新后，检查登录、需求/任务、历史附件，以及右上角用户菜单、个人设置和手机排期。

升级失败需要回退时，先停机，按 [恢复步骤](../../README.md#10-恢复)恢复本次数据库和附件备份，并恢复 CONFIG_BACKUP 中的配置。迁移后不能只切回 v0.1 镜像，旧版会重新显示软删除的数据。
