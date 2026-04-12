# 四层边界：评分核心 / 学习闭环 / 治理闭环 / 运维巡检

## 1. 目标

本轮不是把系统改成新的平台，而是把当前仓库已有能力按四层重新收口：

1. 评分核心层
2. 学习闭环层
3. 治理闭环层
4. 运维巡检层

要求保持：

- 最终分数仍由确定性规则裁决
- 学习与治理不能直接改线上最终分
- 运维巡检只守门，不参与评分裁决
- agent 只能输出候选信息

## 2. 当前仓库到四层的映射

### 评分核心层

职责：

- 规则评分
- V2 评分
- 证据映射
- 16 维聚合
- 可解释输出

当前主要模块：

- `app/engine/scorer.py`
- `app/engine/v2_scorer.py`
- `app/engine/compare.py`
- `app/engine/evidence.py`
- `app/engine/evidence_units.py`
- `app/domain/scoring/core.py`

边界要求：

- 尽量纯函数
- 同输入同版本同配置可重放
- 不直接依赖学习闭环或运维巡检结果

### 学习闭环层

职责：

- 真实评标结果回灌
- 校准器训练
- 特征蒸馏
- 反射与演化
- 学习画像

当前主要模块：

- `app/feedback_learning.py`
- `app/ground_truth_intake.py`
- `app/engine/calibrator.py`
- `app/engine/feature_distillation.py`
- `app/engine/reflection.py`
- `app/domain/learning/loop.py`

边界要求：

- 运行方式应为离线或准离线
- 只生成候选校准与候选特征
- 不直接覆盖线上最终评分结果

### 治理闭环层

职责：

- 采纳 / 忽略 / 回退 / 人工确认
- 变更审批
- 影响分析
- 版本切换
- 审计留痕

当前主要模块：

- `app/feedback_governance.py`
- `app/domain/governance/loop.py`
- `app/storage.py` 中的版本快照、回滚、事件记录

边界要求：

- 任何学习产物、候选变更都先进入治理层
- 治理层决定是否生效
- 治理动作必须全量留痕

### 运维巡检层

职责：

- `health`
- `ready`
- `self-check`
- `doctor`
- `soak`
- `trial-preflight`
- `acceptance`
- `ops-agents`

当前主要模块：

- `app/system_health.py`
- `app/trial_preflight.py`
- `app/engine/ops_agents.py`
- `app/domain/ops/guard.py`

边界要求：

- 只观测、诊断、守门
- 不能直接修改评分裁决
- 不能直接发布学习结果或线上配置

## 3. 新增的边界收口点

### 领域门面

为避免继续把边界混在 `app/main.py`、`runtime.py` 或散落 engine 中，本轮新增：

- `app/domain/scoring/core.py`
- `app/domain/learning/loop.py`
- `app/domain/governance/loop.py`
- `app/domain/ops/guard.py`

这些模块的作用不是重写旧逻辑，而是：

- 给上层提供稳定入口
- 把“读快照 / 算候选 / 做守门”收口
- 为后续继续拆 legacy runtime 提供支点

## 4. 允许跨层的方向

只允许以下方向：

```text
interfaces -> application -> domain -> infrastructure
                           -> storage/event ports
```

不允许：

- 接口层直接调用 `app.storage` 细节
- 接口层直接调 `app.engine.*`
- 运维层直接改评分结果
- 学习层直接改生产裁决
- agent 直接改分数、权重、配置

## 5. 本轮落地的三类受控 agent 与四层关系

### EvidenceCompletenessAgent

- 所属边界：评分核心层之上的受控辅助
- 读取：施组提交与评分结果
- 输出：候选证据缺口、候选补充建议
- 不做：改分、改规则

### ScoreDeviationAnalysisAgent

- 所属边界：学习闭环与治理闭环之间
- 读取：真实评标、提交、评分结果
- 输出：候选校准建议、候选特征调整建议、候选治理复核建议
- 不做：直接部署校准器或特征包

### OpsTriageAgent

- 所属边界：运维巡检层
- 读取：`ops_agents` / `doctor` / `soak` / `preflight` / `acceptance` 快照
- 输出：异常分级、处置建议
- 不做：改评分裁决、改线上配置

## 6. 哪些地方一旦改错会破坏可解释性

以下位置必须谨慎：

- `app/engine/scorer.py`
- `app/engine/v2_scorer.py`
- `app/engine/compare.py`
- `app/domain/scoring/core.py`
- 任何分制换算逻辑
- 任何证据命中与维度聚合逻辑

风险原因：

- 会直接影响总分、16 维度分、证据来源、扣分项解释
- 一旦把学习/agent 输出直接混入最终裁决，系统会退化成黑盒评分器

## 7. 当前仍然保留的混叠点

本轮只完成了边界收口，未完成彻底搬迁。当前仍存在：

- `app/feedback_learning.py` 仍依赖 `app.main` 的部分 legacy helper
- `app/feedback_governance.py` 仍依赖 `app.main` 的部分 legacy helper
- `app/engine/ops_agents.py` 仍保留内部子 agent 编排

当前处理方式：

- 通过 `app/domain/*` 和 `app/application/agents/*` 增加上层统一入口
- 通过 `OpsTriageAgent` 把现有 `ops_agents` 的结果统一汇总进受控 agent 审计链

## 8. 下一步拆分建议

P0：

- 继续把 learning / governance 中对 `app.main` 的 helper 依赖迁到 `app/domain/*`

P1：

- 把 `app/engine/ops_agents.py` 的内部子 agent 也纳入统一注册与调度机制

P2：

- 为四层增加更明确的回放命令和差异报告命令
