# 青天专项阶段交付索引

## 1. 索引用途

本文用于汇总青天专项阶段标签、对应 PR 成果、阶段验证摘要、安全边界和回退定位，服务后续交接、验收与复盘。

本文只做阶段交付索引：
- 不改变任何运行逻辑；
- 不接核心评分主链；
- 不改变数据库 / data 写入结构；
- 不替代详细测试报告和 PR 记录。

## 2. 阶段标签总表

| 阶段标签 | commit | 阶段主题 | 覆盖 PR | 回退用途 | 阶段状态 |
|---|---|---|---|---|---|
| `v0.1.7-qingtian-copy-export` | `a45bab4bea07ca594fb87f874262eb2ffcee025c` | compare_report 复制/导出、Ollama preview 边界、copy/export polish | #19-#26 | 可作为 compare / Ollama preview / copy export 阶段稳定点 | 已合并并打标签 |
| `v0.1.8-qingtian-health-stability` | `8fb8093d7dc37b2a35e0d842bc6e033017198094` | 健康稳定运行命令边界、ops_agents runtime boundary、start_ops_agents 启动前提示 | #27-#29 | 可作为健康稳定运行命令边界稳定点 | 已合并并打标签 |
| `v0.1.9-qingtian-health-selfcheck-boundaries` | `745e7a14330072db072f33579c9321619b874085` | health / ready / self_check 运行态边界 | #30 | 可作为 health / self_check 边界稳定点 | 已合并并打标签 |
| `v0.1.10-qingtian-diagnostic-scripts-boundaries` | `f98f35199556cac2dfb1ca18a677af85d1b09a4b` | diagnostic scripts 副作用边界 | #31 | 可作为 diagnostic scripts 副作用边界稳定点 | 已合并并打标签 |

## 3. PR 总表

| PR | 标题 | 阶段归属 | merge commit | 主要成果 |
|---|---|---|---|---|
| PR #19 | fix: structure compare recommendation copy fields | `v0.1.7-qingtian-copy-export` | `00d2d7276175d1fad390eeaa087fbb3f5e8837e3` | 结构化 compare recommendation 复制字段 |
| PR #20 | fix: clarify ollama preview safety boundary | `v0.1.7-qingtian-copy-export` | `82f466d79971289e6e715f32893f44909a7e6121` | 明确 Ollama preview 安全边界 |
| PR #21 | test: cover ollama preview copy export guards | `v0.1.7-qingtian-copy-export` | `38030e96e2682768e1b68e1a55cb05635dfa5a97` | 覆盖 Ollama preview 复制 / 导出 guard |
| PR #22 | test: guard ollama preview backend contract | `v0.1.7-qingtian-copy-export` | `cf6fbf79d5355e16ab7e289384c925fc4360e429` | 锁定 Ollama preview 后端契约 |
| PR #23 | docs: clarify ollama preview boundaries | `v0.1.7-qingtian-copy-export` | `ae2981526dfaa095c637360a31d58e9f7d56b3a7` | 补充 Ollama preview 边界文档 |
| PR #24 | fix: add compare report copy export actions | `v0.1.7-qingtian-copy-export` | `dce6568ae28202c93319fab30e1dc3f1fdc93fe3` | 增加 compare_report 优化清单复制 / JSON 导出 |
| PR #25 | docs: document compare report copy export actions | `v0.1.7-qingtian-copy-export` | `ec4c67bec2326691c04130febf0031c38823b128` | 说明 compare_report 复制 / 导出行为 |
| PR #26 | fix: refine compare report copy text formatting | `v0.1.7-qingtian-copy-export` | `a45bab4bea07ca594fb87f874262eb2ffcee025c` | 优化 compare_report 复制文本格式 |
| PR #27 | docs: add health stability command boundaries | `v0.1.8-qingtian-health-stability` | `42e0a1696f5a91ad34a913b23facc1926630a093` | 增加健康稳定运行命令边界说明 |
| PR #28 | test: guard ops agents runtime boundaries | `v0.1.8-qingtian-health-stability` | `95ac2642511f92a16971c5a1a8377e4030988e03` | 增加 ops_agents runtime boundary 静态断言 |
| PR #29 | fix: warn before starting ops agents | `v0.1.8-qingtian-health-stability` | `8fb8093d7dc37b2a35e0d842bc6e033017198094` | 为 start_ops_agents.sh 增加启动前安全提示 |
| PR #30 | test: guard health self check runtime boundaries | `v0.1.9-qingtian-health-selfcheck-boundaries` | `745e7a14330072db072f33579c9321619b874085` | 增加 health / ready / self_check 边界文档与静态断言 |
| PR #31 | test: guard diagnostic script runtime boundaries | `v0.1.10-qingtian-diagnostic-scripts-boundaries` | `f98f35199556cac2dfb1ca18a677af85d1b09a4b` | 增加 diagnostic scripts 副作用边界文档与静态断言 |

## 4. 每阶段成果摘要

### v0.1.7 compare / Ollama preview / copy export 阶段

- compare recommendation 字段已结构化，便于复制和导出时保留原文、替换文本、补充内容、验收标准和执行检查表。
- Ollama preview 维持人工预览边界，仅用于预览，不写正式学习进化结果。
- Ollama preview 与 compare_report copy/export 不影响评分，不接核心评分主链。
- compare_report 优化清单支持复制和 JSON 导出。
- compare_report copy text formatting 已优化，包含生成时间并跳过空字段。

### v0.1.8 健康稳定运行阶段

- 新增健康稳定运行命令边界说明。
- ops_agents runtime boundary 已有静态测试。
- `start_ops_agents.sh` 启动前已有安全提示。
- 明确 `--auto-repair 1`、`--auto-evolve 1`、`build/ops_agents.log`、`build/ops_agents.pid` 等运行态边界。
- 未改 `app/engine/ops_agents.py`。
- 未改 `scripts/ops_agents.py`。

### v0.1.9 health / self_check 边界阶段

- `/health` 是轻量 liveness 检查。
- `/ready` 是运行态 readiness 检查。
- 当前实现中 `/ready` 可能调用 `ensure_data_dirs`。
- `self_check` 是运行态诊断。
- `self_check` 会触达 data，并使用 `selfcheck_*.tmp` 临时文件。
- `ready` / `self_check` 不应称为纯只读检查。
- 当前边界下默认不连接 Ollama。
- `self_check` 不作为核心评分主链入口。

### v0.1.10 diagnostic scripts 副作用边界阶段

- `doctor.sh` 不是纯只读检查。
- `restart_server.sh` 是服务控制脚本。
- `data_hygiene.sh` 区分 audit 与 `APPLY=1` repair。
- `e2e_api_flow.sh` 是端到端写入验证。
- `server_status.sh` 不是 `git grep` 类静态检查。
- 不应在只读阶段执行这些脚本。
- 执行前需要单独授权。

## 5. 阶段验证摘要

| 阶段标签 | 验证摘要 | 是否启动服务 | 是否连接 Ollama | 是否写 data |
|---|---|---|---|---|
| `v0.1.7-qingtian-copy-export` | 阶段回归 29 passed，文档关键词检查通过 | 否 | 否 | 否 |
| `v0.1.8-qingtian-health-stability` | `bash -n` start/stop/status ops agent scripts 通过，`py_compile` 通过，pytest 14 passed，文档关键词检查通过 | 否 | 否 | 否 |
| `v0.1.9-qingtian-health-selfcheck-boundaries` | `py_compile` 通过，pytest 11 passed，文档关键词检查通过 | 否 | 否 | 否 |
| `v0.1.10-qingtian-diagnostic-scripts-boundaries` | `py_compile` 通过，pytest 1 passed，文档关键词检查通过；未执行 `scripts/*.sh`，未运行 `bash -n`，未启动/停止/重启服务，未发起 curl 请求 | 否 | 否 | 否 |

## 6. 安全边界总览

- 未接核心评分主链。
- 未修改 `scorer.py`。
- 未修改 `v2_scorer.py`。
- 未修改 `storage.py`。
- 未改变数据库 / data 写入结构。
- 未提交 `.env`。
- 未提交密钥。
- 文档检查、静态检查、mock 测试不需要 `ollama serve`。
- 真实 Ollama 调用前才需要用户在 2 号窗口运行 `ollama serve`。
- 服务启动、restart、doctor、data_hygiene repair、e2e_api_flow 均不是默认只读动作。

## 7. 回退定位说明

- `v0.1.7-qingtian-copy-export` 可作为 compare / Ollama preview / copy export 阶段稳定点。
- `v0.1.8-qingtian-health-stability` 可作为健康稳定运行命令边界稳定点。
- `v0.1.9-qingtian-health-selfcheck-boundaries` 可作为 health / self_check 边界稳定点。
- `v0.1.10-qingtian-diagnostic-scripts-boundaries` 可作为 diagnostic scripts 副作用边界稳定点。
- 本文只提供标签定位，不提供 reset、force push、git clean 等破坏性命令。

## 8. 后续专项建议

- 继续采用“小任务、单分支、单 PR、CI 通过后合并”的节奏。
- 新专项先做只读审计，再决定是否修改。
- 后续仍不接核心评分主链，除非用户明确授权。
