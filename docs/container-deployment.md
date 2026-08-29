# R6D2 容器部署与交付

## 安全启动

容器交付以 Python 3.12、单 Uvicorn worker、SQLite/WAL 为固定生产配置。先创建本地
secret 文件，再构建和启动：

```bash
umask 077
mkdir -p secrets
python3 -c 'import secrets; print(secrets.token_urlsafe(48))' > secrets/api_keys.txt
docker compose build --pull
docker compose up -d
docker compose ps
```

默认只绑定 `127.0.0.1:8000`。浏览器访问 `http://127.0.0.1:8000/`，将
`secrets/api_keys.txt` 中的 key 保存到页面认证区。禁止提交 `secrets/`、`.env`、数据卷
或备份文件。

## 启动门禁

生产入口在启动 Uvicorn 前执行以下 fail-closed 检查：

- API key 必须存在且不能是示例占位值；
- 数据目录和 SQLite 路径必须为绝对路径；
- 数据卷必须可写，评分配置必须可加载；
- 存储后端必须为 JSON 或 SQLite，默认 SQLite；
- SQLite 必须通过 `integrity_check`；
- `WEB_CONCURRENCY` 必须为 1。当前进程内缓存尚未跨 worker 协调，禁止用多 worker
  换取表面吞吐。

入口只输出无密钥的公开配置摘要。API key 通过 Compose secret 文件加载到进程环境，
不写入镜像、Compose 文件或启动日志。

## 持久化与权限

- 容器以 UID/GID `10001:10001` 非 root 运行；
- 根文件系统只读，全部 Linux capabilities 被移除，并启用 `no-new-privileges`；
- `/tmp` 使用受限 tmpfs；
- `qingtian_data` 保存 SQLite/WAL 和上传资料；
- `qingtian_config` 保存可热更新的 `active_config.yaml` 及评分配置；
- 日志轮转为 10 MiB × 3，停止宽限期为 40 秒。

首次创建 `qingtian_config` 卷时，Docker 会复制镜像内置的默认资源。不要把空宿主目录
直接覆盖到 `/srv/qingtian/app/resources`。

## 健康检查与验收

镜像内健康检查同时验证：

- `/health` 返回 `healthy`；
- `/ready` 返回 `ready`；
- readiness 中 `config` 与 `data_dirs` 全部为真。

```bash
docker compose exec app python scripts/container_healthcheck.py
docker compose exec app id
curl -fsS http://127.0.0.1:8000/health
curl -fsS http://127.0.0.1:8000/ready
```

本交付镜像已按与 Compose 相同的安全边界完成本地认证：容器以 UID 10001 和只读根文件
系统运行，数据卷可写；无 API key 请求返回 401，有效 key 请求返回 200；`chi_sim` OCR
语言包可用；SQLite `integrity_check` 返回 `ok`；创建项目后销毁并重建容器，项目仍能从同一
数据卷读取。CI 使用同一组只读根文件系统、能力移除、非提权和持久卷参数重复健康验证。

## 备份、恢复与回滚

备份必须同时覆盖数据卷和配置卷。执行卷级备份前先停止应用，避免复制进行中的 WAL：

```bash
docker compose stop app
docker run --rm -v qingtian_qingtian_data:/source:ro -v "$PWD/backups":/backup \
  alpine:3.22 tar -czf /backup/qingtian-data.tgz -C /source .
docker run --rm -v qingtian_qingtian_config:/source:ro -v "$PWD/backups":/backup \
  alpine:3.22 tar -czf /backup/qingtian-config.tgz -C /source .
docker compose start app
```

恢复时必须先保留当前卷，再将备份恢复到新卷并运行容器健康检查；不得在运行中的卷上
覆盖。镜像回滚通过 `QINGTIAN_IMAGE=<已认证镜像标签> docker compose up -d` 完成，数据
格式回滚必须先执行 R4D3 迁移/恢复验证，不能只回退镜像。

若需要从其他主机访问，应在 TLS 反向代理后显式设置 `QINGTIAN_BIND_ADDRESS` 和可信
`FORWARDED_ALLOW_IPS`；不要直接把 Uvicorn 端口暴露到公网。
