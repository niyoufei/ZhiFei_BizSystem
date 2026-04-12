# 故障排查表

## 1. 创建项目时报网络错误

优先排查：

```bash
curl -s http://127.0.0.1:8000/health
curl -s http://127.0.0.1:8000/ready
curl -s http://127.0.0.1:8000/api/v1/system/self_check
```

常见原因：

- 服务未启动
- `ready` 未通过，前端调用失败
- 配置未完整加载
- 数据目录无写权限

## 2. `storage_lock_status=false`

现象：

- 上传、评分、学习动作卡住
- `self_check` 中锁状态失败

排查：

```bash
python3 - <<'PY'
from app import storage
print(storage.probe_storage_lock_status())
PY
```

处理：

- 确认是否有长时间占用的批处理或解析进程
- 清理异常退出残留任务
- 若为共享目录，检查文件系统锁兼容性

## 3. `event_log_appendability=false`

现象：

- 评分或治理动作完成，但无审计事件
- `self_check` 中事件日志检查失败

排查：

```bash
python3 - <<'PY'
from app import storage
print(storage.build_storage_backend_status())
print(storage.probe_event_log_appendability())
PY
```

处理：

- 检查 `ZHIFEI_STORAGE_ENABLE_EVENT_LOG`
- 检查事件库目录是否可写
- 检查 SQLite 文件是否被其他进程长事务占用

## 4. `projection_consistency=false`

现象：

- 审计汇总与事件明细不一致
- 项目统计异常

排查：

```bash
python3 - <<'PY'
from app import storage
print(storage.probe_projection_consistency())
print(storage.replay_project_activity_projection(persist=False))
PY
```

处理：

- 先不要继续治理或学习写入
- 重新执行投影重放
- 若快照损坏，可依赖事件日志全量重建

## 5. `scoring_replay_consistency=false`

现象：

- 同一施组重复评分结果漂移
- 验收或自检报告出现 replay mismatch

排查：

```bash
make replay-regression
```

处理：

- 检查是否引入了非确定性排序或时间相关逻辑
- 检查模型候选证据是否错误进入最终 accepted evidence
- 检查学习权重是否在未治理情况下直接影响线上裁决

## 6. `learning_artifact_versions=false`

现象：

- 学习产物存在，但无法回退
- 校准器或高分特征变更不可审计

排查：

```bash
python3 - <<'PY'
from app import storage
print(storage.probe_learning_artifact_versions())
PY
```

处理：

- 先停止继续写入学习产物
- 检查 `save_calibration_models` / `save_high_score_features` 是否走了统一存储层
- 补齐版本快照后再恢复学习闭环

## 7. `agent_dependency_health=false`

现象：

- agent dry-run 失败
- `ops-agents` 无法产出诊断

排查：

```bash
python3 scripts/ops_agents.py --interval-seconds 0 --max-cycles 1
curl -s http://127.0.0.1:8000/api/v1/system/self_check
```

处理：

- 检查 agent 审计目录可写
- 检查默认 agent 注册是否完整
- 检查模型边界服务是否处于 `no-model` 或 credentials missing 状态

## 8. 现场照片解析慢或卡住

优先区分：

- `queued`
  只是排队，通常不是故障。
- `processing`
  需要看 backlog 和 worker 状态。
- `failed`
  看 `parse_error`。

排查：

```bash
curl -s "http://127.0.0.1:8000/api/v1/system/self_check?project_id=<PROJECT_ID>"
```

重点看：

- `vision_parse_queue_healthy`
- `material_parse_backlog_ok`
- `gpt_parse_failure_rate_ok`

## 9. 学习后效果变差

排查：

1. 看最近 `ActualResultRecorded`
2. 看 `CalibratorTrained`
3. 看 `FeaturePackUpdated`
4. 看 `RollbackApplied`

建议动作：

- 先回退校准器版本
- 再看是否需要忽略最新治理建议
- 最后重新执行 `trial-preflight`
