# 发布质量门

## 1. 最低通过标准

发布前必须至少通过以下质量门：

1. `make lint`
2. `make typecheck`
3. `make test`
4. `make replay-regression`
5. `make doctor`
6. `make acceptance-fast`

若本次发布涉及真实项目试车，还必须追加：

7. `make trial-preflight PROJECT_ID=<PROJECT_ID>`
8. `make soak SOAK_DURATION=600 SOAK_INTERVAL=30`

## 2. 各门槛的意义

- `lint`
  保证基础静态质量和导入结构。
- `typecheck`
  保证新增稳定层和运维层的类型约束不漂移。
- `test`
  保证主链路和历史兼容测试不过期。
- `replay-regression`
  保证评分在固定输入上可重放、可复现。
- `doctor`
  保证 API 和系统自检链路正常。
- `acceptance-fast`
  保证端到端主要链路未断。
- `trial-preflight`
  保证当前试车项目具备闭环前条件。
- `soak`
  保证长时间运行没有明显退化。

## 3. 阻断条件

出现以下任一情况，禁止发版：

- `ready` 返回 `not_ready`
- `self_check.required_ok` 为 `false`
- `storage_lock_status` 失败
- `event_log_appendability` 失败
- `projection_consistency` 失败
- `scoring_replay_consistency` 失败
- `learning_artifact_versions` 失败
- `agent_dependency_health` 失败
- `RollbackApplied` 无法成功执行

## 4. 回归基线

### 4.1 评分回放

- 同一输入连续运行 30 次
- 总分、16 维分、证据哈希完全一致

### 4.2 学习闭环

- 录入真实评标结果后，新增校准器或高分特征变更必须能解释
- 学习产物必须存在版本快照
- 任意版本必须能回退，并生成 `RollbackApplied`

### 4.3 治理闭环

- 采纳 / 忽略 / 回退都必须生成审计事件
- 变更不得直接绕过治理写入生产裁决结果

## 5. 推荐发版顺序

```bash
make quality-gate
make doctor
make acceptance-fast
make trial-preflight PROJECT_ID=<PROJECT_ID>
```

若以上全部通过，再进入正式上线或 Windows 安全部署包交付。

## 6. 回滚触发条件

任意以下情况满足时，应优先回滚：

- 评分重放签名与历史不一致
- 最新校准器 MAE 明显劣化
- 投影快照与事件全量重建不一致
- 事件日志无法追加
- `agent` 或 `ops` 守门链路持续失败

## 7. 回滚顺序

1. 回退学习产物
2. 回退治理变更
3. 重新执行 `self_check`
4. 重新执行 `doctor`
5. 重新执行 `trial-preflight`
