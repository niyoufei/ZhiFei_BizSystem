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

## 三、推荐的最小健康回归

以下组合不需要启动服务，不需要 Ollama，但 pytest 可能写 `.pytest_cache/` 或 `__pycache__/`，应放在验证轮执行：

```bash
python3 -m pytest -q \
  tests/test_main.py::TestHealthEndpoints \
  tests/test_main.py::TestSystemSelfCheckCapabilities \
  tests/test_v2_pipeline.py::TestSystemSelfCheckEndpoint \
  tests/test_ops_agents.py
```

## 四、禁止边界

- 不把 health / self_check / ops_agents 接入核心评分主链。
- 不修改 `scorer.py`。
- 不修改 `v2_scorer.py`。
- 不修改 `storage.py`。
- 不改变数据库 / `data/` 写入结构。
- 不提交 `.env`。
- 不提交密钥。
- 不运行 `git clean`。
- 不把真实 Ollama 调用作为默认回归。

## 五、后续任务建议

- 为 ops_agents 启动脚本补充安全提示或静态断言。
- 为 health / self_check 增加更清晰的只读 / 运行态说明。
- 补充健康专项最小回归清单。
