# QingTian v1.1.0-rc.3 生产准入候选

## 基线

- 主线基线：`aca8a3ba4aa416ebf1ce074f92a999d05798963b`
- 前序候选：`v1.1.0-rc.1`
- RC1 镜像：`ghcr.io/niyoufei/zhifei-bizsystem@sha256:d2d1c8c0d4003340cb5ed1571df49ec587359d3340972a02ce2396f45fbb790b`
- RC3 包含 RC2 的可观测性和本机隔离认证基础设施，并修复 Linux 非 root 容器读取
  bind-mounted secret 的权限问题；不改变业务 API 或存储 schema。

## 发布门禁

- Python 3.10、3.11、3.12 全量测试、Ruff、OpenAPI 合同和发布版本验证必须通过。
- 依赖、源代码和候选镜像不得包含可修复的 HIGH/CRITICAL 漏洞或未豁免密钥。
- Prometheus 配置和告警规则必须通过 `promtool`，实际 target 必须为 UP。
- 应用与裁剪加固后的 Grafana 镜像必须提供 linux/amd64、linux/arm64、SBOM、
  provenance 和不可变 digest。
- Prometheus 与 Alertmanager 使用上游精确 digest；Grafana 由上游 slim 镜像和经
  SHA-256 校验的官方 Prometheus datasource 构建，禁止运行时插件下载或更新。

## R8 终止边界

本 Release 创建时仍是 Pre-release。只有同一精确 digest 完成 72 小时连续试运行、
停机备份、全新卷恢复和 RC1 回滚演练后，才能追加无敏感数据的生产准入证书。
任何代码、配置、依赖或 digest 变化都会使已有观察证据失效。

R8 不创建 `v1.1.0` 稳定标签，也不执行公网或生产数据部署。
