# 施工组织设计评标评分系统 Runbook

## 1. 目标

本手册用于本地部署、企业内网部署和运维值守场景，覆盖以下链路：

- API/Web 启动与存活确认
- 评分、学习、治理、agent、ops 五类任务的结构化日志定位
- health / ready / self-check / doctor / trial-preflight / acceptance / soak 的执行顺序
- 事件日志、投影、回放一致性、学习产物版本的巡检与回退

## 2. 关键日志字段

第五轮改造后，主链路日志统一带以下上下文字段：

- `correlation_id`
  用于串联一次请求或一次命令链路。
- `project_id`
  用于标识项目级任务。
- `run_id`
  用于标识一次具体执行。
- `task_kind`
  取值：`scoring` / `learning` / `governance` / `agent` / `ops`
- `task_name`
  取值示例：`score_submission_text`、`system_self_check`
- `task_state`
  取值：`running` / `succeeded` / `failed` / `timed_out` / `degraded` / `cached`
- `failure_category`
  取值示例：`validation` / `storage` / `event_log` / `projection` / `replay_consistency`

建议日志检索方式：

```bash
rg '"correlation_id"' build logs data -g '*.log' -g '*.jsonl'
rg '"task_state":"failed"' build logs data -g '*.log' -g '*.jsonl'
rg '"project_id":"<PROJECT_ID>"' build logs data -g '*.log' -g '*.jsonl'
```

## 3. 标准执行顺序

### 3.1 启动与基础检查

```bash
make run
curl -s http://127.0.0.1:8000/health
curl -s http://127.0.0.1:8000/ready
curl -s http://127.0.0.1:8000/api/v1/system/self_check
```

判定标准：

- `/health`
  只做 liveness，进程存活即可。
- `/ready`
  必须通过配置、数据目录、配置完整性、锁状态、事件日志追加性检查。
- `/api/v1/system/self_check`
  用于综合守门，包含可选退化项。

### 3.2 本地质量门

```bash
make quality-gate
```

当前质量门包含：

- `ruff`
- `mypy`（受控范围）
- `pytest`
- `replay-regression`

### 3.3 运维巡检链路

```bash
make doctor
make soak SOAK_DURATION=600 SOAK_INTERVAL=30
make trial-preflight PROJECT_ID=<PROJECT_ID>
make acceptance
```

建议顺序：

1. `doctor`
   用于后端自检和接口契约检查。
2. `trial-preflight`
   用于单项目试车前体检。
3. `soak`
   用于长时间稳定性观测。
4. `acceptance`
   用于严格回归验收。

## 4. self-check 关键探针说明

当前 `self_check` 至少覆盖以下项目：

- `config`
  配置可加载。
- `config_completeness`
  配置不是“可加载但缺 rubric/lexicon 或维度定义”。
- `data_dirs_writable`
  数据目录可写。
- `storage_backend_status`
  当前主存储、镜像和事件日志开关状态。
- `storage_lock_status`
  存储锁可获取，未发生锁阻塞。
- `event_log_appendability`
  事件日志后端可进入写事务。
- `projection_consistency`
  投影快照与从事件全量重建结果一致。
- `scoring_replay_consistency`
  有评分样本时，重放结果与已存结果一致；无样本时明确标记 `skipped`。
- `learning_artifact_versions`
  校准器、高分特征、进化报告存在版本快照。
- `agent_dependency_health`
  agent 注册、审计写入目录与模型边界服务状态正常。

## 5. 事件日志与投影

### 5.1 关键事件

- `ProjectCreated`
- `ArtifactUploaded`
- `ScoreComputed`
- `ActualResultRecorded`
- `CalibratorTrained`
- `FeaturePackUpdated`
- `GovernanceDecisionApplied`
- `RollbackApplied`
- `OpsCheckExecuted`

### 5.2 审计核查

```bash
python3 - <<'PY'
from app import storage
for row in storage.list_domain_events(event_types=["GovernanceDecisionApplied", "RollbackApplied"]):
    print(row["event_type"], row["aggregate_id"], row["payload"])
PY
```

### 5.3 投影重放

```bash
python3 - <<'PY'
from app import storage
payload = storage.replay_project_activity_projection(persist=False)
print(payload["projection_name"], payload["project_count"], payload["last_sequence"])
print(storage.probe_projection_consistency())
PY
```

## 6. 学习与回退

学习产物仍然保留 JSON 兼容路径，但必须带版本快照。

### 6.1 查看历史版本

```bash
python3 - <<'PY'
from app import storage
print(storage.list_json_versions(storage.CALIBRATION_MODELS_PATH))
print(storage.list_json_versions(storage.HIGH_SCORE_FEATURES_PATH))
PY
```

### 6.2 回退

```bash
python3 - <<'PY'
from app import storage
versions = storage.list_json_versions(storage.CALIBRATION_MODELS_PATH)
if versions:
    print(storage.restore_json_version(storage.CALIBRATION_MODELS_PATH, versions[-1]["version_id"]))
PY
```

回退后会自动追加 `RollbackApplied` 审计事件。

## 7. 评分重放回归

```bash
make replay-regression
```

当前回归基线要求：

- 同一输入重复运行 30 次
- 总分一致
- 16 维分一致
- 证据哈希一致

## 8. 日常值守建议

### 8.1 每日巡检

```bash
make doctor
make data-hygiene
```

### 8.2 发版前

```bash
make quality-gate
make acceptance-fast
make trial-preflight PROJECT_ID=<PROJECT_ID>
```

### 8.3 故障后

1. 先看 `correlation_id / run_id`
2. 再看 `self_check` 中是否是 `storage_lock_status`、`event_log_appendability`、`projection_consistency` 失败
3. 若涉及学习产物，先查看版本快照，再执行回退
