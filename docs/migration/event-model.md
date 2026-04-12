# 事件模型（第二轮骨架）

## 1. 事件表结构

事件存储当前使用 SQLite，表名 `event_log`，关键字段如下：

- `sequence_no`
- `event_id`
- `aggregate_type`
- `aggregate_id`
- `event_type`
- `event_version`
- `payload_json`
- `occurred_at`
- `actor_type`
- `actor_id`
- `causation_id`
- `correlation_id`
- `idempotency_key`
- `metadata_json`

其中：

- `sequence_no` 用于顺序回放
- `idempotency_key` 用于幂等去重
- `payload_json` 保留事件业务上下文
- `metadata_json` 预留审计附加信息

## 2. 当前事件类型

### `ProjectCreated`

触发点：

- 新建项目
- 通过招标文件自动识别并创建项目

最小载荷：

- `project_id`
- `name`
- `project_type`
- `bid_method`

### `ArtifactUploaded`

触发点：

- 上传招标文件 / 答疑
- 上传清单 / 图纸 / 现场照片
- 上传施组

最小载荷：

- `project_id`
- `artifact_id`
- `artifact_type`
- `filename`
- `path`

### `ScoreComputed`

触发点：

- 施组评分完成并落库

最小载荷：

- `project_id`
- `submission_id`
- `total_score`
- `scoring_engine_version`
- `dimension_count`
- `evidence_unit_count`

### `ActualResultRecorded`

触发点：

- 录入真实评标结果
- 批量录入真实评标结果

最小载荷：

- `project_id`
- `ground_truth_id`
- `source`
- `final_score`
- 可选 `submission_id`

### `CalibratorTrained`

触发点：

- `save_calibration_models(...)` 检测到新版本校准器

最小载荷：

- `collection`
- `version`
- `project_id`
- `metrics`

### `FeaturePackUpdated`

触发点：

- `save_high_score_features(...)` 检测到新增特征包条目

最小载荷：

- `collection`
- `feature_id`
- `project_id`
- `feature_name`

### `GovernanceDecisionApplied`

触发点：

- guardrail 审核动作执行
- few-shot 审核动作执行

最小载荷：

- `project_id`
- `record_id`
- `review_type`
- `action`

### `RollbackApplied`

触发点：

- `restore_json_version(...)`

最小载荷：

- `collection`
- `version_id`
- `backup_version_id`
- `path`

### `OpsCheckExecuted`

触发点：

- system self-check
- data hygiene
- system improvement overview

最小载荷：

- `check_type`
- 可选 `project_id`
- 可选 `apply`

## 3. 幂等规则

- 项目创建：`project-created:<project_id>`
- 资料上传：`artifact-uploaded:<artifact_id>`
- 评分完成：`score-computed:<submission_id>`
- 真实评标录入：`ground-truth:<ground_truth_id>`
- 校准器训练：`calibrator-trained:<version>`
- 特征包更新：`feature-pack-updated:<feature_id>`
- 治理决策：`governance-...`
- 运维巡检：当前不去重，每次执行都记录

## 4. Projection / Replay

首批建议的 projection：

- `project_activity`

用途：

- 统计每个项目的创建、资料上传、评分、真实结果回灌、校准、特征包更新、治理动作、回滚、巡检次数
- 提供项目级审计视图

回放命令：

```bash
python3 -m app.cli storage replay
python3 -m app.cli storage replay --persist
```

## 5. 审计字段建议

下一阶段建议继续补齐：

- `rule_version`
- `weight_version`
- `model_version`
- `prompt_version`
- `operator_source`
- `request_id`
- `project_month`

本轮没有把这些字段强行写死到所有事件里，原因是当前旧链路仍存在大量兼容入口，先保证事件骨架落地和可回放。
