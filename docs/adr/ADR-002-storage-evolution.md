# ADR-002：存储演进为“元数据数据库 + 事件日志 + 文件存储”的混合方案，并保留 JSON 兼容层

- 状态：Accepted
- 日期：2026-04-12

## 背景

当前仓库的存储基线并不差，`app/storage.py` 已经提供了：

- 原子写
- 文件锁
- JSON 历史版本快照
- Windows 安全桌面下的 DPAPI 加密

但审计也确认了三个现实问题：

1. 结构化实体越来越多，跨实体联查成本越来越高。
2. 事件审计靠快照和当前状态倒推，不足以支撑精确回放。
3. 存在多处绕过 `app/storage.py` 的旁路文件读写。

因此，问题不是“JSON 一无是处”，而是“当前系统已经同时需要三种不同形态的存储能力”。

## 决策

引入三类明确边界的存储角色：

1. `Repository`：结构化元数据读写
2. `EventStore`：append-only 领域事件日志
3. `ArtifactStore`：原始资料和大对象文件存储

同时保留现有 JSON 路径，作为第一阶段兼容实现和回退基线。

## 为什么文件型存储需要演进为混合方案

### 1. 文件快照擅长保存对象，不擅长回答关系问题

系统当前的主链路已经天然是多实体关系：

- project
- material
- parse job
- submission
- score report
- evidence unit
- ground truth
- delta case
- calibration sample
- governance decision

继续把这些全部当独立 JSON 快照处理，会让以下查询越来越贵：

- 一个项目下的所有评分轮次及其证据索引
- 某次真实评标回灌触发了哪些学习产物刷新
- 某个治理动作到底影响了哪些项目或版本

### 2. 文件快照不是严格事件日志

快照能回答“现在是什么”，但不擅长精确回答：

- 谁触发了这次评分
- 哪个上传动作导致了哪次解析
- 哪个真实评标结果触发了哪次校准器训练
- 哪次治理动作采纳或回退了哪条变更

这些都是 append-only 事件模型更擅长的问题。

### 3. 原始资料和大对象仍应保留在文件系统

本系统长期处理：

- 招标文件 / 答疑
- 清单
- 图纸
- 现场照片
- 施组原文
- markdown / docx / report 等导出物

这些资料天然适合文件存储，不适合强行塞进数据库 BLOB 作为主路径。

## 与当前仓库的直接对应

### 当前基线与规划落点

| 现状 | 当前基线 / 规划落点 |
| --- | --- |
| JSON 统一门面 | `app/storage.py` |
| 安全桌面 DPAPI 文件加密 | `app/storage.py` + `app/windows_desktop.py` |
| 高风险治理快照 | `save_*(..., keep_history=True)` in `app/storage.py` |
| 旁路文件读写热点（需收口） | `app/main.py`、`app/cli.py`、`app/web_ui.py` |

### 目标落点

| 角色 | 目标目录 |
| --- | --- |
| Repository 接口 | `app/ports/repositories.py` |
| EventStore 接口 | `app/ports/event_store.py` |
| ArtifactStore 接口 | `app/ports/artifact_store.py` |
| JSON 兼容实现 | `app/infrastructure/storage/json_compat.py` |
| SQLite 元数据实现 | `app/infrastructure/storage/sqlite_metadata.py` |
| 事件日志实现 | `app/infrastructure/storage/sqlite_event_store.py` |
| 文件存储实现 | `app/infrastructure/storage/file_store.py` |
| 兼容 facade | `app/storage.py` |

## 哪些数据继续适合文件存储

这些内容继续保留在文件存储中：

1. 原始招标文件、答疑、清单、图纸、照片、施组
2. OCR 中间产物、文本抽取缓存、预览文件
3. 评分导出物、markdown/docx/pdf 报告
4. 安全桌面下需要 DPAPI 包裹的文件型资料

这些对象的共同特点是：

- 体积较大
- 来源文件需要原样保留
- 存在二进制或版式语义
- 更适合路径引用而不是整块读写

## 哪些数据必须入库

第一阶段应优先纳入数据库索引或主存储的结构化实体：

1. `projects`
2. `materials`
3. `material_parse_jobs`
4. `submissions`
5. `score_reports` 的结构化摘要索引
6. `evidence_units` 的索引与关联关系
7. `ground_truth_records`
8. `qingtian_results`
9. `delta_cases`
10. `calibration_samples`
11. `calibration_models` 元数据
12. `expert_profiles` 元数据
13. `evolution_reports` 元数据
14. `patch_packages` / `patch_deployments`
15. 治理动作、人工确认、回退记录
16. 任务状态、幂等键、事件偏移

这些对象的共同特点是：

- 需要联查
- 需要分页/筛选/排序
- 需要精确审计
- 需要回放

## 事件日志模型

事件日志至少覆盖以下事件类型：

- `ProjectCreated`
- `ArtifactUploaded`
- `ScoreComputed`
- `ActualResultRecorded`
- `CalibratorTrained`
- `FeaturePackUpdated`
- `GovernanceDecisionApplied`
- `RollbackApplied`
- `OpsCheckExecuted`

建议最低字段：

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

## 数据库选择

### 第一阶段：SQLite

采纳原因：

- 满足本地部署和企业内网约束
- 运维成本低
- 足够支撑结构化索引和 append-only 事件表
- 便于和当前 JSON / 文件路径并存

### 后续预留：Postgres 适配口，不强行落地

只预留接口，不在当前阶段引入额外部署复杂度。

## 迁移策略

### Phase 1：接口抽象，不改主读路径

- 定义 `Repository` / `EventStore` / `ArtifactStore`
- `app/storage.py` 继续作为 JSON 兼容 facade
- SQLite 先作为镜像索引或受控双写目标

### Phase 2：关键实体双写 + 事件落盘

- 项目、材料、评分、真实结果、治理动作进入 SQLite
- 同步写入 append-only 事件
- 读路径仍可按开关回到 JSON

### Phase 3：结构化查询切换到数据库主读

- JSON 降级为兼容回放源和应急回退源
- 审计视图和重建视图优先走事件日志 + projection

## 约束

1. 不允许直接废弃 JSON。
2. 不允许业务层直接读写 JSON 文件。
3. 任何双写都必须有校验脚本和回退开关。
4. 任何影响评分结果的存储迁移都必须有回放比对。

## 后果

### 正向收益

- 结构化查询、审计、治理预览、回放能力增强。
- 文件资料仍可按当前模式保留，减少迁移阻力。
- 为多用户、高并发和幂等控制打基础。

### 成本

- 双写阶段需要对账和幂等保障。
- 存储边界会从一个文件扩展为三类角色，设计复杂度上升。

## 回滚策略

1. 保留 JSON 主写或主读开关。
2. 任一数据库/事件日志问题出现时，可退回 JSON 兼容路径。
3. 文件资料路径不迁移、不改名，避免资料层面的不可逆变更。

## 不采纳方案

### 方案 A：继续只用 JSON

不采纳原因：

- 无法优雅支撑结构化联查和事件回放

### 方案 B：直接把所有内容塞进数据库

不采纳原因：

- 不符合本系统处理大文件、二进制资料和安全桌面文件加密的现实
