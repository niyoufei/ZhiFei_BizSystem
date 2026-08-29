# R8 本机隔离试运行与生产准入

## 不可变输入

R8 只允许使用 `v1.1.0-rc.4` Release 中 `release-digest.txt` 记录的应用与 Grafana
完整 `ghcr.io/...@sha256:...` 镜像。不得使用可移动 tag、工作区构建镜像或生产数据。

创建宿主私有目录，并为不同 UID 的非 root 容器提供只读 secret 文件：

```bash
install -d -m 0700 secrets
python3 -c 'import secrets; print(secrets.token_urlsafe(48))' > secrets/api_keys.txt
python3 -c 'import secrets; print(secrets.token_urlsafe(48))' > secrets/grafana_admin_password.txt
chmod 0644 secrets/api_keys.txt secrets/grafana_admin_password.txt
```

文件本身需要对容器 UID 可读；宿主侧由不可遍历的 `0700` 父目录限制访问。文件仅以只读
方式挂载到对应容器，且始终保持未跟踪。

## 启动隔离环境

将两个占位符替换为 Release 中的完整镜像引用：

```bash
export COMPOSE_PROJECT_NAME=qingtian-r8
export QINGTIAN_IMAGE='<RC4_IMAGE_AT_DIGEST>'
export R8_GRAFANA_IMAGE='<RC4_GRAFANA_IMAGE_AT_DIGEST>'
export QINGTIAN_BIND_ADDRESS=127.0.0.1
export QINGTIAN_PORT=18080
docker compose -f compose.yaml -f docker-compose.monitoring.yml up -d
docker compose -f compose.yaml -f docker-compose.monitoring.yml ps
```

应用、Prometheus、Alertmanager 和 Grafana 分别只绑定本机端口 18080、19090、19093、
13000。四个服务必须处于同一 `qingtian-r8` 隔离项目，数据、配置和监控卷不得复用其他环境。

## 72 小时连续观察

每次正式观察使用新的输出目录，禁止覆盖或续跑已中断报告：

```bash
mkdir -p build/r8/soak-01
caffeinate -dimsu python -m scripts.staging_soak \
  --base-url http://127.0.0.1:18080 \
  --prometheus-url http://127.0.0.1:19090 \
  --api-key-file secrets/api_keys.txt \
  --expected-image '<RC4_IMAGE_AT_DIGEST>' \
  --container qingtian-r8-app-1 \
  --output-dir build/r8/soak-01
```

睡眠、进程中断、容器重启、OOM、digest 漂移或任一探针失败都会把报告封存为 BLOCKED。
不得修改报告继续累计；应排查后使用新的 RC 和新的输出目录重新执行完整 72 小时。

## 停机备份、恢复与回滚演练

仅在同一精确 digest 的 soak 报告为 PASS 后执行：

```bash
python -m scripts.staging_drill \
  --api-key-file secrets/api_keys.txt \
  --candidate-image '<RC4_IMAGE_AT_DIGEST>' \
  --rc1-image 'ghcr.io/niyoufei/zhifei-bizsystem@sha256:d2d1c8c0d4003340cb5ed1571df49ec587359d3340972a02ce2396f45fbb790b' \
  --output-dir build/r8/drill-01
```

脚本先停止原应用，再离线 checkpoint、校验完整性并固定逻辑指纹，然后将数据卷和配置卷
备份到本机忽略目录并恢复到全新卷；
随后依次验证 RC4、RC1 回滚和 RC4 恢复。原应用在演练结束或失败后均会尝试恢复启动。
备份压缩包不得上传，只有不含业务载荷的报告和 SHA-256 清单摘要可以进入 Release 资产。

## PASS 边界

- `soak-report.json` 为 PASS，窗口不少于 72 小时；
- `drill-report.json` 为 PASS，RPO=0、RTO≤900 秒；
- 源、恢复 RC4、回滚 RC1、再次恢复 RC4 的逻辑指纹一致；
- Release digest、运行容器 digest 和本地认证 ref 指向同一 RC4 源提交。

满足全部条件后，才创建 `refs/qingtian/certified/r8-production-readiness` 并把两个无敏感
报告及备份清单摘要上传到 RC4 Pre-release。R8 不创建稳定标签。
