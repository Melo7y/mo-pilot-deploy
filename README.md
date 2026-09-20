# MoPilot 公网生产部署

本目录提供 MoPilot 的 Docker Compose 生产部署包。本文只给出操作步骤；仓库维护过程不会连接或修改任何服务器。

部署拓扑如下：

```text
公网 80/443
     |
  Nginx + 前端静态文件
     |
  Docker 内部网络
     |------- Go API
     |          |------- 本地附件目录
     |
     |------- PostgreSQL
     |
     `------- 每日备份容器
```

只有 Nginx 映射宿主机端口。Go API 和 PostgreSQL 均不映射端口，不能从公网直接访问。

## 1. 文件说明

| 文件 | 用途 |
| --- | --- |
| `compose.yaml` | PostgreSQL、迁移、API、Web、备份和初始化工具 |
| `.env.example` | 生产环境变量模板，不包含管理员密码 |
| `Taskfile.yml` | 常用部署命令，使用 go-task |
| `nginx/default.conf.template` | HTTPS、SPA、API 代理、安全头和登录限流 |
| `scripts/backup.sh` | PostgreSQL 与附件备份、保留期清理 |
| `fail2ban/` | 登录失败过滤器与 Docker 防火墙封禁示例 |
| `logrotate/` | Nginx 日志轮转示例 |

前端和后端镜像分别由相邻的 `frontend`、`backend` 目录构建。生产环境不会运行开发 seed，不会创建 `zhaoke`、`hanmei` 等演示账号。

## 2. 服务器前提

建议使用仍在安全支持期内的 Linux 发行版，并准备：

- 一个已经解析到服务器公网 IP 的域名。
- Docker Engine、Docker Compose v2、go-task、Certbot、Fail2Ban。
- 至少 2 核 CPU、4 GB 内存和足够保存附件及 30 天备份的磁盘。
- 使用 SSH 密钥登录，关闭 SSH 密码登录和 root 远程登录。
- 云安全组及宿主机防火墙只开放 SSH 管理端口、TCP 80 和 TCP 443。
- 不开放 PostgreSQL 5432 和 API 8080。
- 系统时间同步正常，时区使用 `Asia/Shanghai`。

附件保存在服务器本地目录。50 人、在线 10 人以内时，本地文件系统性能足够；真正需要关注的是磁盘容量、备份和单机故障，而不是吞吐量。

推荐目录结构：

```text
/opt/mopilot/
├── backend/
├── frontend/
└── deploy/

/srv/mopilot/
├── attachments/
├── backups/
├── acme/
└── logs/nginx/
```

将三个仓库分别检出到上述目录，部署仓库目录命名为 `deploy`。如果使用其他位置，在 `.env` 中调整 `BACKEND_CONTEXT`、`FRONTEND_CONTEXT` 和 `MOPILOT_DATA_DIR`。

## 3. 准备环境变量和目录

进入部署目录：

```bash
cd /opt/mopilot/deploy
cp .env.example .env
chmod 600 .env
openssl rand -hex 32
```

将最后一条命令的结果填入 `.env` 的 `POSTGRES_PASSWORD`。建议只使用生成的十六进制值，避免数据库连接 URL 中的特殊字符转义问题。至少修改：

```dotenv
MOPILOT_DOMAIN=mopilot.example.com
MOPILOT_IMAGE_TAG=20260919-1
POSTGRES_PASSWORD=生成的随机十六进制值
BOOTSTRAP_ADMIN_NAME=系统管理员
BOOTSTRAP_ADMIN_USERNAME=admin
BOOTSTRAP_ADMIN_TEAM=杭州
```

每次发布使用新的 `MOPILOT_IMAGE_TAG`，不要反复覆盖同一个标签。`.env` 已被 Git 忽略，不得提交，也不要把 `docker compose config` 的完整输出粘贴到工单或聊天中，因为展开结果含数据库密码。

创建持久化目录：

```bash
sudo install -d -m 0750 /srv/mopilot/attachments
sudo install -d -m 0750 /srv/mopilot/backups
sudo install -d -m 0755 /srv/mopilot/acme
sudo install -d -m 0750 /srv/mopilot/logs/nginx
sudo chown -R 10001:10001 /srv/mopilot/attachments
```

API 镜像以 UID/GID `10001:10001` 运行，因此附件目录必须允许该用户写入。PostgreSQL 数据使用 Docker 命名卷，附件、备份和日志使用明确的宿主机目录。

## 4. 首次申请 HTTPS 证书

确认 DNS 已生效，并确保 80 端口尚未被其他程序占用：

```bash
sudo certbot certonly --standalone \
  --agree-tos \
  --no-eff-email \
  --email ops@example.com \
  -d mopilot.example.com
```

把邮箱和域名替换为真实值。证书应出现在：

```text
/etc/letsencrypt/live/mopilot.example.com/fullchain.pem
/etc/letsencrypt/live/mopilot.example.com/privkey.pem
```

证书存在前不要启动 Web 容器，否则 Nginx 会因找不到证书而退出。

Web 启动后，将续期认证方式改为已挂载的 ACME webroot：

```bash
sudo certbot reconfigure \
  --cert-name mopilot.example.com \
  --webroot \
  --webroot-path /srv/mopilot/acme
sudo certbot renew --dry-run
```

如果服务器上的 Certbot 版本不支持 `reconfigure`，应按该版本官方文档把现有证书的续期 authenticator 改为 webroot，不能让自动续期继续占用已由 Nginx 使用的 80 端口。

证书成功续期后需要重载 Nginx。可在 `/etc/letsencrypt/renewal-hooks/deploy/mopilot-nginx-reload.sh` 中放置：

```sh
#!/bin/sh
cd /opt/mopilot/deploy
docker compose --env-file .env -f compose.yaml exec -T web nginx -s reload
```

该脚本由 root 管理并设置为可执行。先运行 `certbot renew --dry-run`，确认 webroot 校验和 deploy hook 都成功。

## 5. 首次部署

先校验配置并构建镜像：

```bash
cd /opt/mopilot/deploy
task config
task build
task db:up
task migrate
```

创建首个管理员。密码至少 8 个字符、最多 72 字节，建议使用密码管理器生成 16 位以上随机密码。密码只通过当前 shell 环境传入，不写入 `.env`：

```bash
read -rsp '首个管理员密码: ' BOOTSTRAP_ADMIN_PASSWORD
echo
export BOOTSTRAP_ADMIN_PASSWORD
task bootstrap-admin
unset BOOTSTRAP_ADMIN_PASSWORD
```

初始化命令具有以下保护：

- 团队和管理员在同一个数据库事务内创建。
- 密码只保存 bcrypt 哈希，日志不会输出密码。
- 数据库中只要已有任意管理员，命令就拒绝再次执行。
- 初始化命令不会创建演示用户或演示项目。

随后启动全部服务：

```bash
task up
task ps
```

访问 `https://你的域名` 登录。首次登录后立即在“用户管理”中创建第二个独立管理员作为应急账号，并安全保管其密码。不要用首个管理员账号做锁定或限流测试。

## 6. 上线验证

检查容器和 API 健康状态：

```bash
task ps
docker compose --env-file .env -f compose.yaml logs --tail=100 web api migrate backup
curl -fsS https://你的域名/
```

确认宿主机只监听预期端口：

```bash
sudo ss -lntp
```

预期公网服务只有 80 和 443；列表中不应出现 Docker 发布的 5432 或 8080。再检查：

- HTTP 自动跳转 HTTPS。
- 证书域名和有效期正确。
- 登录、退出、附件上传和附件下载正常。
- 刷新需求或任务详情页不会返回 Nginx 404。
- 浏览器控制台没有 CSP 阻止 MoPilot 自身资源。
- `docker compose ... exec web nginx -t` 通过。

HSTS 默认开启一年，但没有启用 `includeSubDomains` 和 preload。确认 HTTPS 长期稳定后再评估是否扩大范围。

## 7. 登录防爆破

公网登录采用三层保护：

1. 应用按账号记录失败次数，连续失败 5 次后锁定 5 分钟，状态保存在 PostgreSQL。
2. Nginx 同时限制 `/api/v1/auth/login` 和 `/api/login/account`，默认每个 IP 持续 `10r/m`、突发 10 次，超限返回统一 JSON 429。
3. Fail2Ban 在 10 分钟内看到同一 IP 15 次 401 或 423 后封禁 1 小时，重复攻击最长 24 小时。

Nginx 登录专用日志只记录 IP、时间、请求行、状态、耗时和 User-Agent，不记录密码、请求体、Cookie 或 `Authorization`。

安装仓库提供的配置：

```bash
sudo cp fail2ban/filter.d/mopilot-login.conf /etc/fail2ban/filter.d/
sudo cp fail2ban/jail.d/mopilot-login.local /etc/fail2ban/jail.d/
sudo cp logrotate/mopilot-nginx /etc/logrotate.d/mopilot-nginx
sudo fail2ban-client -t
sudo systemctl restart fail2ban
sudo fail2ban-client status mopilot-login
```

如果 `MOPILOT_DATA_DIR` 不是 `/srv/mopilot`，必须同步修改 Fail2Ban 和 logrotate 中的日志路径。

Docker 发布端口的流量通常经过 `DOCKER-USER` 链，而不是普通 `INPUT` 链。不同发行版可能使用 nftables 后端，必须从另一台机器验证被封 IP 确实无法访问，不能只看 Fail2Ban 列表。若当前系统的 Docker 不经过该链，应根据发行版选择对应 action。

使用临时普通用户验证账号锁定：前 4 次错误密码应返回 401，第 5 次返回 423 和 `ACCOUNT_LOCKED`，5 分钟后正确密码可再次登录。不要使用管理员账号测试。

使用不存在的用户名验证入口限流：

```bash
for index in $(seq 1 25); do
  curl -s -o /dev/null -w '%{http_code}\n' \
    -H 'Content-Type: application/json' \
    -d "{\"username\":\"rate-test-$index\",\"password\":\"wrong-password\"}" \
    https://你的域名/api/login/account
done
```

预期部分请求返回 429，并包含 `Retry-After: 60`。对 `/api/v1/auth/login` 也要重复验证。

公司多人可能共用一个公网出口 IP。发生误封时先核对日志并临时解封明确的办公出口 IP，再把持续速率从 `10r/m` 调整为 `20r/m` 或把 burst 调整为 20。不要直接关闭账号锁定、Nginx 限流或 Fail2Ban。

## 8. 备份

`backup` 服务启动后立即备份一次，之后默认每 24 小时备份一次，保留 30 天。每个备份目录包含：

```text
backup-YYYYMMDDTHHMMSSZ/
├── database.dump
├── attachments.tar.gz
├── manifest.txt
└── SHA256SUMS
```

立即执行一次备份：

```bash
task backup
```

检查最新备份：

```bash
ls -lah /srv/mopilot/backups
cd /srv/mopilot/backups/backup-具体时间
sha256sum -c SHA256SUMS
```

本机备份不能应对整台服务器或云盘损坏。至少每天把完整的 `backup-*` 目录加密同步到另一台服务器或对象存储，并定期在隔离环境做恢复演练。目标恢复点默认是 24 小时；需要更小 RPO 时缩短 `BACKUP_INTERVAL_SECONDS`。

备份顺序是先 PostgreSQL、后附件。当前附件只追加，不提供删除操作，因此这能避免数据库引用尚未进入附件归档的文件。以后若增加附件删除或替换，应改为存储快照或短暂停写备份。

## 9. 恢复

恢复会覆盖当前数据库和附件，必须进入维护窗口，并先确认备份校验通过。以下命令中的备份目录必须替换为已核对的明确路径。

先停止外部访问和写入，只保留数据库：

```bash
cd /opt/mopilot/deploy
docker compose --env-file .env -f compose.yaml stop web api backup
docker compose --env-file .env -f compose.yaml up -d db
```

校验备份：

```bash
cd /srv/mopilot/backups/backup-具体时间
sha256sum -c SHA256SUMS
```

重新创建数据库并恢复。下一步会删除当前数据库内容：

```bash
cd /opt/mopilot/deploy
docker compose --env-file .env -f compose.yaml exec -T db sh -ec \
  'dropdb -U "$POSTGRES_USER" --if-exists "$POSTGRES_DB"; createdb -U "$POSTGRES_USER" "$POSTGRES_DB"'
docker compose --env-file .env -f compose.yaml exec -T db sh -ec \
  'pg_restore -U "$POSTGRES_USER" -d "$POSTGRES_DB"' \
  < /srv/mopilot/backups/backup-具体时间/database.dump
```

保留当前附件作为回退副本，再恢复附件：

```bash
sudo mv /srv/mopilot/attachments /srv/mopilot/attachments.before-restore
sudo install -d -m 0750 /srv/mopilot/attachments
sudo tar -xzf /srv/mopilot/backups/backup-具体时间/attachments.tar.gz \
  -C /srv/mopilot/attachments
sudo chown -R 10001:10001 /srv/mopilot/attachments
```

执行当前版本迁移并恢复服务：

```bash
cd /opt/mopilot/deploy
task migrate
task up
task ps
```

登录并抽查需求、任务、附件和时间线后，才可以清理 `attachments.before-restore`。恢复演练必须使用隔离的 Compose project 和目录，不能在生产环境直接试验。

## 10. 升级与回滚

升级前：

1. 执行 `task backup` 并校验 `SHA256SUMS`。
2. 记录三个代码仓库的 commit、当前 `MOPILOT_IMAGE_TAG` 和数据库迁移状态。
3. 拉取经过测试的明确版本，不直接部署未固定的分支最新提交。
4. 在 `.env` 中设置新的唯一镜像标签。

执行升级：

```bash
task config
task build
task migrate
task up
task ps
```

升级后完成登录、列表、详情、附件上传下载和关键流程冒烟测试，并观察 API/Nginx 日志。

应用回滚时，把 `.env` 的 `MOPILOT_IMAGE_TAG` 改回仍保留在本机的旧标签，再执行：

```bash
docker compose --env-file .env -f compose.yaml up -d --no-build api web
```

只有确认数据库迁移向后兼容时才能只回滚应用。涉及不兼容 schema 或数据变化时，应进入维护窗口并按上一节恢复升级前备份，不要盲目执行 `migrate down`。

## 11. 日常运维

```bash
task ps
task logs
sudo fail2ban-client status mopilot-login
sudo tail -n 100 /srv/mopilot/logs/nginx/mopilot-login.log
df -h
docker system df
sudo certbot certificates
```

建议为以下事件配置监控告警：

- Web 或 API 健康检查失败。
- PostgreSQL 不可用或连接数异常。
- 备份容器退出、24 小时内没有新备份、校验失败。
- 数据盘使用率超过 70% 和 85%。
- TLS 证书将在 30 天内到期。
- 单个 IP 持续产生 401、423、429，或多个 IP 轮流尝试同一账号。
- 管理员账号触发锁定。

应用日志由 `docker compose logs` 查看；生产服务器还应配置 Docker 日志大小和轮转策略，避免 stdout 日志占满系统盘。

## 12. 公网安全边界

- 保持操作系统、Docker、PostgreSQL、Nginx 和基础镜像更新，并先在隔离环境验证升级。
- `.env`、证书私钥、数据库备份和附件备份只允许运维账号读取。
- 定期审查管理员、PM 和已禁用账号，离职账号立即禁用并重置相关共享凭据。
- 不在日志、工单、截图或聊天中发送密码、Cookie、数据库 URL 和备份文件。
- 若前面增加 CDN、云负载均衡或 WAF，必须只信任明确的代理 IP，并正确配置真实客户端 IP；否则所有人会被识别为同一代理 IP，限流和 Fail2Ban 都会失真。
- 三层防爆破不能完全防住大规模分布式密码喷洒。持续受到此类攻击时，应增加云 WAF，并优先推进钉钉免登、SSO 或 MFA。
- 本方案是单机部署，数据库和附件仍有单机故障窗口。业务重要性提升后，应把 PostgreSQL、附件存储和备份迁移到具备多副本与跨机容灾的托管服务。
