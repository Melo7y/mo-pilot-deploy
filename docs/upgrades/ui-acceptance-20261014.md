# v0.1 → v0.2 停机升级

前后端一起更新，数据库从 00008 迁移到 00011。v0.2 验收镜像统一使用 **v0.2.0-rc.1**，包含 UI 修复和新 Logo。

## 1. 打包

在构建机（也可以是测试服务器）的三个仓库父目录执行：

~~~bash
bash mo-pilot-deploy/scripts/package-v0.2.sh linux/amd64
~~~

服务器若是 ARM，最后一个参数改为 linux/arm64。成功结束会打印 **Package ready: 完整路径**，该目录应有两个 tar.gz、source.txt 和 SHA256SUMS。

同一台服务器打包和升级时，直接用生成目录，无需复制到 /tmp。只有在另一台机器打包时才需要上传四个文件，并将下方 PACKAGE 改为实际上传目录。

## 2—5. 停机备份 → 导入 → 迁移启动 → 检查

确认升级包已生成后，复制下面**完整代码块**执行。命令运行于独立 Bash，失败会中止升级并返回登录 shell，避免交互式 set -e 断开 SSH。部署目录按现有环境修改，数据库和附件目录沿用原 .env；保持原 Compose 项目名。如果原启动命令有 -p 或额外的 -f，把它们原样加到 dc 函数中。

~~~bash
bash <<'UPGRADE' || echo '升级已中止，请先处理上方错误。'
set -euo pipefail
cd /home/melo/code/mo-pilot/mo-pilot-deploy
TAG=v0.2.0-rc.1
PACKAGE="../.scratch/v0.2-package/$TAG"
CONFIG_BACKUP="$HOME/mopilot-upgrade-backups/config-before-v0.2-$(date +%Y%m%d-%H%M%S)"
# 关闭 Compose 的 stdin，避免 run 吞掉 here-document 中后续升级命令。
dc() { docker compose --env-file .env -f compose.yaml "$@" </dev/null; }

# 四个文件完整并通过校验后，才开始停机。
for file in SHA256SUMS source.txt "mopilot-images-$TAG.tar.gz" "mopilot-deploy-$TAG.tar.gz"; do
  if [ ! -f "$PACKAGE/$file" ]; then
    echo "缺少升级文件：$PACKAGE/$file；请先完成打包或修正 PACKAGE。" >&2
    exit 1
  fi
done
(cd "$PACKAGE" && sha256sum -c SHA256SUMS)

echo '2. 停机并备份'
umask 077
mkdir -p -m 0700 "$CONFIG_BACKUP"
cp -R .env compose*.yaml nginx scripts "$CONFIG_BACKUP/"
echo "配置备份：$CONFIG_BACKUP"
dc stop web api backup
dc run --rm -T --pull never backup once
dc run --rm -T --pull never --entrypoint /bin/sh backup -ec '
  BACKUP="$(find /backups -maxdepth 1 -type d -name "backup-*" | sort | tail -n 1)"
  test -n "$BACKUP"
  cd "$BACKUP"
  sha256sum -c SHA256SUMS
'

echo '3. 导入前后端镜像，更新部署文件'
gzip -dc "$PACKAGE/mopilot-images-$TAG.tar.gz" | docker load
tar -xzf "$PACKAGE/mopilot-deploy-$TAG.tar.gz" -C .
sed -i "s/^MOPILOT_IMAGE_TAG=.*/MOPILOT_IMAGE_TAG=$TAG/" .env
grep -qx "MOPILOT_IMAGE_TAG=$TAG" .env
dc config --quiet

echo '4. 迁移数据库，启动前后端'
dc run --rm -T --pull never migrate up
dc up -d --no-build --pull never --remove-orphans

echo '5. 检查'
dc ps
dc run --rm -T --pull never migrate status
dc exec -T web nginx -t
UPGRADE
~~~

配置备份保存在当前用户的 ~/mopilot-upgrade-backups/，目录权限为 0700；数据库和附件备份由 backup 容器保存并校验，无需登录用户读写 /srv/mopilot/backups。

输出会依次出现第 2、3、4、5 步。只有 backup ready 表示备份完成，升级仍需继续到第 5 步。若旧版命令在此处直接返回提示符，应使用上述关闭 stdin 的 dc 定义，校验已经生成的备份后从第 3 步继续。

原 .env、数据库卷、附件和证书保留。若有自定义 Nginx 配置，升级前将对应修改合入新模板。不要执行 seed、bootstrap-admin 或 down -v。

确认 API、Web、数据库健康，迁移为 **00011**。浏览器硬刷新后，检查登录、需求/任务、历史附件，以及右上角用户菜单、个人设置和手机排期。

升级失败需要回退时，先停机，按 [恢复步骤](../../README.md#10-恢复)恢复本次数据库和附件备份，并恢复代码块输出的配置备份目录。迁移后不能只切回 v0.1 镜像，旧版会重新显示软删除的数据。
