# 健康稳定运行命令边界说明

## 一、适用范围

本文用于区分 ZhiFei_BizSystem 健康稳定运行相关命令的副作用边界，包括健康检查、运行状态检查、诊断脚本、服务启动、ops agents、Ollama 预览等场景。

本文只说明命令和入口的使用边界，不改变任何评分逻辑，不接核心评分主链，不改变数据库或 `data/` 写入结构。

## 二、命令边界总表

| 类别 | 命令或入口 | 是否启动服务 | 是否连接 Ollama | 是否可能写入文件 | 适用场景 | 使用边界 |
|------|------------|--------------|-----------------|------------------|----------|----------|
| 只读检查 | `git status`、`git grep`、`rg`、`find`、`cat` / `sed` 查看文件 | 否 | 否 | 否 | 审计前置检查、文件定位、链路梳理 | 不启动服务、不连接 Ollama、不写 `data/`，适合只读体检 |
| TestClient / mock 测试 | health / self_check 相关 pytest 测试、ops_agents mock 测试 | 否 | 否 | 可能写 `.pytest_cache/`、`__pycache__/` | 修改后回归、PR 前验证 | 不需要启动服务、不需要 Ollama；因可能产生缓存，应放在验证轮执行 |
| py_compile / 静态验证 | `python3 -m py_compile` | 否 | 否 | 可能写 `__pycache__/` | 语法验证、提交前检查 | 不启动服务、不连接 Ollama；因可能产生缓存，应放在验证轮执行 |
| 服务启动入口 | `python3 -m app.main`、`make run`、`make api`、`scripts/start.sh`、`scripts/restart_server.sh` | 是 | 否 | 可能写 `build/server.pid`、`build/server.log` | 本地运行、运行态验收 | 会启动或重启服务，需单独授权，不在只读体检中执行 |
| health / ready / self_check 运行态接口 | `/health`、`/ready`、`/api/v1/system/self_check`、`/api/system/self_check` | 否 | 否 | `/ready` / `self_check` 可能触达 config 和 data dirs | 已启动服务后的状态确认 | `/health` 适合存活检查；`/ready` / `self_check` 不作为纯只读检查使用 |
| ops_agents / watchdog | `scripts/start_ops_agents.sh`、`scripts/stop_ops_agents.sh`、`scripts/ops_agents_status.sh`、`scripts/ops_agents.py`、`app/engine/ops_agents.py` | `start_ops_agents.sh` 会启动守护 | 否 | 可能写 `build/ops_agents_status.json`、`build/ops_agents_status.md`、日志、pid | 运维巡检、守护、状态聚合 | `start_ops_agents.sh` 默认涉及 auto-repair / auto-evolve 风险，需单独授权 |
| Ollama 真实调用 | Ollama 增强预览、`OLLAMA_BASE_URL`、`OLLAMA_MODEL` | 否 | 是 | 不应写正式报告；真实调用会访问本机 Ollama | 手动预览增强结果 | 真实调用前必须提醒用户先在 2 号窗口运行 `ollama serve`；文档检查、静态检查、mock 测试不需要 `ollama serve` |

## 三、health / ready / self_check 边界说明

### `/health`

当前实现适合作为最轻量 liveness 检查，只返回服务存活状态。它不触达 `data/`，不写文件，不调用评分主链，不连接 Ollama。

### `/ready`

当前实现属于运行态 readiness 检查，会读取配置，并调用 `ensure_data_dirs`。因此它可能触达或创建 `data/`、`data/materials/` 目录，不应称为纯只读检查，也不用于静态审计。

### `/api/v1/system/self_check` 与 `/api/system/self_check`

两个路径复用同一 self_check 逻辑，属于运行态诊断。当前实现会检查 config、auth、rate limit、PDF/DOCX/OCR/DWG 能力、data hygiene、项目读取能力，会触达 `data/`，并会创建后删除 `selfcheck_*.tmp` 临时文件用于 data 可写性测试。它不应称为纯只读检查，默认不连接 Ollama，也不作为核心评分主链入口。

### 使用边界

文档检查、`git grep`、静态测试不需要启动服务；TestClient / mock 测试不需要启动服务。真实访问 `/ready` 或 self_check 前，应按运行态诊断处理。只有真实 Ollama 调用才需要提醒用户在 2 号窗口运行 `ollama serve`。

## 四、diagnostic scripts 副作用边界说明

本小节只固定当前边界说明，不改变任何脚本行为。以下脚本不应与 `git grep`、静态测试等纯只读检查混同。

| 脚本 | 边界归类 | 主要副作用 | 是否适合作为只读检查 | 执行前置条件 |
|------|----------|------------|----------------------|--------------|
| `scripts/doctor.sh` | 需单独授权 | 当前实现会发起运行态 HTTP 请求，调用 `/health`、self_check、openapi 相关检查；`/health` 失败时可能调用 `scripts/restart_server.sh`，间接触发服务重启 | 否，脚本不是纯只读检查 | 已明确允许运行态诊断和可能的服务重启 |
| `scripts/restart_server.sh` | 服务控制脚本 | 当前实现会停止旧进程并启动服务，可能使用 `kill` / `kill -9`，并写 `build/server.pid`、`build/server.log`、restart lock | 否，不得在只读审计中执行 | 已明确允许停止 / 启动服务和写 build pid/log |
| `scripts/data_hygiene.sh` | 运行态写入诊断 | 当前实现默认 audit 会发起 HTTP 请求并写 `build/data_hygiene_latest.*`；`APPLY=1` repair 会发起 POST repair，可能通过服务端写 `data/` | 默认 audit 也不是纯只读；repair 更需要单独授权 | 已启动服务；repair 必须额外确认 `APPLY=1` |
| `scripts/e2e_api_flow.sh` | 端到端写入验证 | 当前实现可能启动或重启本地服务，会发起多个 API 请求，触达项目创建、上传、评分、对比、导出、学习或进化链路，并写 `data/` 和 `build/e2e_flow/*` | 否，不适合作为默认回归，不应在只读阶段执行 | 已明确允许端到端写入验证和服务控制 |
| `scripts/server_status.sh` | 运行态状态脚本 | 当前实现可能请求 `/health`；如发现 stale pid，可能修正或删除 `build/server.pid` | 否，不是纯只读静态检查 | 已明确允许运行态状态检查和 pid 文件修正 |

`scripts/doctor.sh` 当前实现不是纯只读检查。它会发起运行态 HTTP 请求，调用 `/health`、`/api/v1/system/self_check`、`/api/system/self_check` 和 `openapi.json` 相关检查；当 health 失败时，可能调用 `scripts/restart_server.sh`，从而间接触发服务重启。执行前需要单独授权。

`scripts/restart_server.sh` 当前实现属于服务控制脚本。它会停止旧进程并启动服务，可能使用 `kill` / `kill -9`，会写 `build/server.pid`、`build/server.log` 和 restart lock，不得在只读审计中执行。执行前需要单独授权。

`scripts/data_hygiene.sh` 当前实现属于运行态诊断脚本。默认 audit 和 `APPLY=1` repair 必须区分：audit 会发起 HTTP 请求并写 `build/data_hygiene_latest.*`；`APPLY=1` repair 会发起 POST repair，可能通过服务端写 `data/`。repair 必须单独授权，不作为默认只读检查。

`scripts/e2e_api_flow.sh` 当前实现属于端到端写入验证。它可能启动或重启本地服务，会发起多个 API 请求，触达项目创建、上传、评分、对比、导出、学习或进化链路，并写 `data/` 和 `build/e2e_flow/*`。它不适合作为默认回归，不应在只读阶段执行。

`scripts/server_status.sh` 当前实现属于运行态状态脚本，可能请求 `/health`。如发现 stale pid 或 pid 文件与监听进程不一致，可能修正或删除 `build/server.pid`，因此不应与 `git grep`、静态测试等纯只读检查混同。

## 五、推荐的最小健康回归

以下组合不需要启动服务，不需要 Ollama，但 pytest 可能写 `.pytest_cache/` 或 `__pycache__/`，应放在验证轮执行：

```bash
python3 -m pytest -q \
  tests/test_main.py::TestHealthEndpoints \
  tests/test_main.py::TestSystemSelfCheckCapabilities \
  tests/test_v2_pipeline.py::TestSystemSelfCheckEndpoint \
  tests/test_ops_agents.py
```

## 六、禁止边界

- 不把 health / self_check / ops_agents 接入核心评分主链。
- 不修改 `scorer.py`。
- 不修改 `v2_scorer.py`。
- 不修改 `storage.py`。
- 不改变数据库 / `data/` 写入结构。
- 不提交 `.env`。
- 不提交密钥。
- 不运行 `git clean`。
- 不把真实 Ollama 调用作为默认回归。

## 七、后续任务建议

- 为 ops_agents 启动脚本补充安全提示或静态断言。
- 为 health / self_check 增加更清晰的只读 / 运行态说明。
- 补充健康专项最小回归清单。
