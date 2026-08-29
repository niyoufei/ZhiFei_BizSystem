# QingTian v1.1.0-rc.1 发布认证

## 基线

- 主线准入基线：`335b825e28fc9017617468a850f9a6400030165a`
- 已认证功能栈：`0fbfd3af67bf29d176259cdad66867924260d208`
- 准入关系：功能栈领先 59 个提交、落后 0，涉及 89 个文件
- 集成方式：单一总集成 PR，使用 merge commit 保留完整认证历史

## 能力闭环

- 核心写链路已完成事务化、补偿恢复与缓存后置。
- Repository/UoW、SQLite/WAL、迁移与恢复链已认证。
- 性能基线、并发可靠性与十场景故障注入已认证。
- 产品交互、无障碍、容器安全、鉴权、健康检查与重启持久化已认证。

## 发布门禁

- Python 3.10、3.11、3.12 全量测试必须全部通过。
- 路由、鉴权、OpenAPI 与冻结运行时合同必须重新认证。
- 运行依赖和候选镜像不得包含可修复的 HIGH 或 CRITICAL 漏洞。
- 框架安全升级通过 OpenAPI 兼容层保持已认证的文件上传与校验错误 schema，仅版本元数据变化。
- 发布镜像必须同时提供 `linux/amd64`、`linux/arm64`、SBOM、provenance 和不可变 digest。
- 发布后只形成 GitHub Pre-release，不进入公网生产环境。

## 本地再认证（2026-08-29）

- Python 3.10、3.11、3.12：各 `1627 passed`，无新增 skip 或 xfail。
- Ruff、pre-commit、actionlint、`git diff --check`、依赖审计与 secret scan：PASS。
- wheel/sdist：构建成功，包版本为 `1.1.0rc1`。
- 候选镜像：非 root、只读根目录、401/200 鉴权、`chi_sim`、SQLite `integrity_check`
  与重启持久化全部 PASS；Trivy 可修复 HIGH/CRITICAL 为 0。
- 远端 tag 工作流将重新执行全部门禁，并记录双架构 GHCR digest、SBOM 与 provenance。

## 回滚边界

RC 回滚以不可变镜像 digest 为单位；数据恢复必须使用 R4D3 已认证的迁移/恢复链和停机卷备份，
不得仅回退镜像覆盖运行中的 SQLite/WAL 数据卷。
