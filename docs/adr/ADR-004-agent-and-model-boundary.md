# ADR-004：模型服务与 Agent 必须受控，规则引擎保留最终裁决权

- 状态：Accepted
- 日期：2026-04-12

## 背景

当前仓库已经存在模型和 agent 相关实现：

- `app/engine/llm_evolution.py`
- `app/engine/llm_evolution_openai.py`
- `app/engine/llm_evolution_gemini.py`
- `app/engine/llm_judge_spark.py`
- `app/engine/ops_agents.py`

系统定位同时非常明确：

- 这是施工组织设计评标评分系统
- 不是 AI 黑盒评分器
- 规则评分必须保留最终裁决权
- 真实评标结果只能驱动学习与治理闭环，不能绕过治理直接改线上裁决

因此，模型和 agent 不是不能用，而是必须被放在正确边界上。

## 决策

模型服务和 agent 只允许提供以下四类候选产物：

1. 候选证据
2. 候选建议
3. 候选变更
4. 运维诊断

它们不能直接改：

1. 最终分数
2. 最终权重
3. 生产评分配置
4. 生产治理状态

## 哪些能力继续保持规则驱动

以下能力必须保持规则驱动和确定性裁决：

### 评分核心

- 最终总分
- 16 维度得分
- 扣分项
- 评分制换算
- 证据门禁
- 跨资料一致性门禁

当前对应模块：

- `app/engine/scorer.py`
- `app/engine/v2_scorer.py`
- `app/engine/evidence.py`
- `app/engine/evidence_units.py`

### 学习与治理裁决

- ground truth 归一化
- 校准器是否满足部署条件
- patch 是否可以采纳
- 版本回滚
- 哪个候选特征包可以生效

当前对应模块：

- `app/feedback_governance.py`
- `app/engine/calibrator.py`
- `runtime.py` 内 ground truth / score scale 解析与治理判定逻辑

### 运维守门

- health / ready
- runtime security
- auth / rate limit
- data hygiene repair 的执行条件

当前对应模块：

- `app/system_health.py`
- `app/runtime_security.py`
- `app/auth.py`
- `app/rate_limit.py`

## 哪些能力适合引入模型服务边界

### 1. 候选证据补全

适用场景：

- 图纸、照片、复杂 PDF、低质量 OCR 的候选信息提取
- 材料与施组之间的候选关联线索

边界：

- 模型只给出候选证据与定位
- 规则层和治理层决定是否接纳

### 2. 候选改写和 narrative 增强

适用场景：

- 对比诊断说明
- 满分优化清单的候选改写内容
- 编制指导文字增强

边界：

- 只改表达，不改分数裁决
- 必须携带来源与版本信息

### 3. 偏差分析与学习建议

适用场景：

- 系统分与真实评标结果偏差分析
- 候选校准策略
- 候选特征调整建议

边界：

- 只能生成 proposal
- 治理层决定是否采纳

### 4. 运维诊断

适用场景：

- 汇总 doctor / soak / preflight / acceptance
- 异常分级和排障建议

边界：

- 只读诊断
- 不自动执行高风险修复

## 受控 Agent 运行时要求

每个 agent 必须有固定协议：

- `agent_name`
- `responsibility`
- `input_schema`
- `output_schema`
- `permission_boundary`
- `timeout_seconds`
- `retry_policy`
- `idempotency_key`
- `audit_fields`

每次执行至少记录：

- `run_id`
- `agent_name`
- `trigger_event`
- `input_digest`
- `output_digest`
- `model_provider`
- `model_name`
- `prompt_version`
- `started_at`
- `finished_at`
- `status`

## 与当前仓库结构的对应演进

### 当前基线与规划收口点

- 模型调用散落在 `app/engine/llm_*`
- 运维 agent 逻辑集中在 `app/engine/ops_agents.py`
- 后续建议通过 `app/contracts/events.py`、`app/contracts/ops.py` 收口未来可复用的契约

### 下一阶段建议落点

| 目标角色 | 建议目录 |
| --- | --- |
| Agent 注册与调度 | `app/application/agents/registry.py`、`runner.py` |
| Agent 契约 | `app/contracts/agents.py` |
| 模型端口 | `app/ports/model_service.py` |
| 模型适配器 | `app/infrastructure/models/*` |
| Agent 审计落盘 | `app/infrastructure/storage/agent_audit.py` |

### 首批规划的 3 个受控 agent

1. `EvidenceCompletenessAgent`
2. `ScoreDeviationAnalysisAgent`
3. `OpsTriageAgent`

这三个 agent 都只能输出 proposal，不直接触碰生产评分。

## 为什么这样做能增强可维护性和可扩展性，同时不破坏可解释性

### 可维护性

- 模型协议统一，减少散落调用
- proposal 可以单独回放和审计
- provider 替换不再侵入评分核心

### 可扩展性

- 可以新增 provider 或 agent，而不改最终裁决逻辑
- 可以逐步增强证据补全、偏差分析、运维诊断能力

### 可解释性

- 最终分数仍来自规则引擎
- 模型只提供候选信息和候选建议
- 采纳路径通过治理层留痕

## 回滚策略

1. 任一模型端口失败，退回规则路径或空 proposal。
2. 任一 agent 出现异常，不阻断评分主链路。
3. proposal 存储失败时，只记录错误，不改最终业务结果。

## 不采纳方案

### 方案 A：让模型直接给最终分数

不采纳原因：

- 破坏确定性
- 破坏可解释性
- 破坏回放一致性

### 方案 B：让 agent 直接改生产配置

不采纳原因：

- 破坏治理闭环
- 破坏审计边界
