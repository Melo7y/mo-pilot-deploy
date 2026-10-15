# v0.1 → v0.2.0-rc.1 停机升级

前后端一起更新，包含 UI 验收修复和新 Logo。迁移从 **00008 → 00011**。

| 参数 | 本次取值 |
| --- | --- |
| `TAG` | `v0.2.0-rc.1` |
| `PACKAGE` | `../.scratch/v0.2-package/v0.2.0-rc.1`，或实际上传目录 |
| `EXPECTED_MIGRATION` | `00011` |
| 前端 commit | `0646cdbb5eea25d44fb7fbd47b8798e50ba9259c` |
| 后端 commit | `931fc13ff34c65391ea6c128457e43ce8007be44` |
| 部署运行配置 commit | `a79ec993a357b8244104c0f93700667d69ca49b1` |

在构建机或测试服务器的三个仓库父目录打包：

```bash
bash mo-pilot-deploy/scripts/package-v0.2.sh linux/amd64
```

ARM 服务器改用 `linux/arm64`。需要构建代理时先导出 `HTTP_PROXY`、`HTTPS_PROXY`。以输出 **Package ready** 的实际目录为准，该目录应包含两份 tar.gz、source.txt 和 SHA256SUMS；同机打包无需复制到 `/tmp`。

随后进入现有部署目录，按 [统一升级流程 B](README.md#b-前后端整套升级) 执行，填写上表参数。保留原 Compose 项目名和额外覆盖文件参数。

本次特殊事项：迁移后不能仅切回 v0.1 镜像，旧版会重新显示软删除数据。需要回退时必须恢复本次数据库、附件与配置备份，按统一流程的回退说明操作。

验收：迁移达到 00011，登录、需求/任务、历史附件正常；右上角用户菜单、个人设置与手机排期正常。本升级包不包含后续的甘特图与需求人列更新；后者见 [前端补丁说明](ui-patch-20261015.md)。
