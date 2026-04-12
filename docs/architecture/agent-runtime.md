# 受控 Agent 运行时

## 1. 目标

本系统允许 agent 提升证据补全、偏差分析和运维诊断能力，但 agent 不是评分裁决器。

因此本轮规划的运行时遵守以下硬边界：

- agent 只能输出候选证据 / 候选建议 / 候选变更 / 运维诊断
- agent 不能直接改最终分数
- agent 不能直接改最终权重
- agent 不能直接改生产配置
- agent 结果必须可审计、可重放、可缓存

## 2. 规划中的代码落点

- 运行时：`app/application/agents/runtime.py`
- 注册表：`app/application/agents/registry.py`
- 应用服务：`app/application/services/agents.py`
- 审计落盘：`app/infrastructure/storage/agent_audit.py`
- 契约：`app/contracts/agents.py`

## 3. 每个 agent 的固定协议

每个 agent 必须提供：

- `agent_name`
- `responsibility`
- `input_schema`
- `output_schema`
- `permission_boundary`
- `timeout_seconds`
- `retry_policy`

首批规划 3 个 agent：

1. `evidence-completeness`
2. `score-deviation-analysis`
3. `ops-triage`

## 4. 审计字段

规划中每次执行写入 `data/agent_audit_log.jsonl`，每条记录至少包含：

| 字段 | 含义 |
| --- | --- |
| `run_id` | 本次执行唯一 ID |
| `agent_name` | agent 名称 |
| `trigger_event` | 触发来源 |
| `idempotency_key` | 幂等键 |
| `input_digest` | 输入摘要 |
| `output_digest` | 输出摘要 |
| `started_at` | 开始时间 |
| `finished_at` | 结束时间 |
| `status` | `success/error/timeout/cached` |
| `attempt_count` | 实际尝试次数 |
| `dry_run` | 是否 dry-run |
| `actor_type` | 执行体类型 |
| `actor_id` | 执行体标识 |
| `model_provider` | 当前固定为 `rules` |
| `model_name` | 当前固定为 `deterministic` |
| `prompt_version` | 当前为空，保留给未来模型型 agent |
| `cache_hit_from_run_id` | 若命中缓存，指向来源 run_id |

## 5. 运行时行为

### 输入校验

- 先按 Pydantic schema 校验
- 非法输入直接返回错误记录

### 幂等

- 默认按 `agent_name + 输入摘要` 生成幂等键
- 相同输入可直接命中缓存

### 超时与重试

- 每个 agent 有显式 `timeout_seconds`
- 每个 agent 有显式 `retry_policy`
- 当前 3 个 agent 都是规则型，默认 1 次尝试

### 缓存

- 当 `reuse_cached=True` 时，会优先查找同幂等键的最近成功记录
- 命中时返回 `cached=true`
- 命中缓存也会追加新的审计记录，不丢失本次触发痕迹

## 6. 权限边界

### EvidenceCompletenessAgent

- 允许读取：提交、评分结果
- 允许写入：agent 审计日志
- 禁止效果：`mutate_score`、`mutate_rules`、`mutate_config`

### ScoreDeviationAnalysisAgent

- 允许读取：项目、提交、真实评标、评分结果
- 允许写入：agent 审计日志
- 禁止效果：`deploy_calibrator`、`activate_feature_pack`、`mutate_score`

### OpsTriageAgent

- 允许读取：`build/ops_agents_status.json`、`build/doctor_summary.json`、`build/stability_soak_latest.json`、`build/trial_preflight_latest.json`、`build/acceptance_summary.json`
- 允许写入：agent 审计日志
- 禁止效果：`restart_runtime`、`mutate_score`、`mutate_config`

## 7. 与现有 ops-agents 的统一方式

历史上仓库已经有 `app/engine/ops_agents.py`。

首轮演进建议不直接重写其所有内部子 agent，而是先做两件事：

1. 保留其原有巡检逻辑，避免大爆炸改造
2. 在 `run_ops_agents_cycle()` 结束后，调用受控 `OpsTriageAgent` 统一产出 `triage` 结果，并写入统一审计日志

这意味着：

- 现有运维巡检能力不丢
- 跨 `doctor/soak/preflight/acceptance` 的汇总诊断进入统一 agent runtime
- 后续可以继续把 `ops_agents.py` 的内部子 agent 逐步迁入统一注册表

## 8. 端到端 dry-run 流程

### 证据完整性 dry-run

```bash
python3 -m app.cli agents dry-run \
  --agent evidence-completeness \
  --project-id <project_id> \
  --submission-id <submission_id> \
  --top-n 5
```

输出包含：

- `candidate_evidence_gaps`
- `candidate_suggestions`
- `audit`

### 偏差分析 dry-run

```bash
python3 -m app.cli agents dry-run \
  --agent score-deviation-analysis \
  --project-id <project_id> \
  --submission-id <submission_id> \
  --ground-truth-id <ground_truth_id>
```

输出包含：

- `actual_score_100`
- `predicted_score_100`
- `delta_score_100`
- `candidate_changes`

### 运维汇总 dry-run

```bash
python3 -m app.cli agents dry-run \
  --agent ops-triage \
  --ops-agents-json build/ops_agents_status.json \
  --soak-json build/stability_soak_latest.json \
  --preflight-json build/trial_preflight_latest.json \
  --acceptance-json build/acceptance_summary.json
```

输出包含：

- `overall_status`
- `severity`
- `diagnostics`
- `recommended_actions`

## 9. 当前限制

- 当前 agent 仍全部是规则型 dry-run agent
- 尚未把模型型 agent 接到统一 runtime
- `app/schemas.py` 兼容层仍指向 legacy 契约，agent 契约当前主要通过 CLI 和内部服务使用

## 10. 下一步

P0：

- 为 agent 运行结果补事件日志投影

P1：

- 把 learning / governance 的候选 proposal 统一纳入治理审批队列

P2：

- 引入模型型 agent 时，复用同一审计与权限边界，而不是新增第二套运行时
