# 存储迁移方案（第二轮骨架）

## 1. 目标实现骨架

- `app/storage.py` 继续是兼容门面，对外保留 `load_* / save_* / list_json_versions / restore_json_version` 等旧调用面。
- 规划新增三类端口：
  - `app/ports/repositories.py`
  - `app/ports/event_store.py`
  - `app/ports/artifact_store.py`
- 规划新增三类基础设施实现：
  - `app/infrastructure/storage/sqlite_metadata.py`
  - `app/infrastructure/storage/sqlite_event_store.py`
  - `app/infrastructure/storage/file_store.py`

## 2. 三类存储职责

### 文件存储

继续留在文件系统的内容：

- 招标文件 / 答疑
- 清单
- 图纸
- 现场照片
- 施组原文
- OCR 中间产物、版式预览、导出报告

规划落点：

- `data/materials/<project_id>/<artifact_type>/...`
- `FileArtifactStore`

### 元数据数据库

需要入库索引或镜像的结构化数据：

- `projects`
- `materials`
- `material_parse_jobs`
- `submissions`
- `score_reports`
- `evidence_units`
- `ground_truth`
- `qingtian_results`
- `calibration_models`
- `high_score_features`
- `patch_packages`
- `patch_deployments`
- `score_history`
- `project_context`

规划落点：

- `SQLiteMetadataRepository`
- 默认数据库：`data/metadata.sqlite3`

### 事件日志

append-only 事件首批建议覆盖：

- `ProjectCreated`
- `ArtifactUploaded`
- `ScoreComputed`
- `ActualResultRecorded`
- `CalibratorTrained`
- `FeaturePackUpdated`
- `GovernanceDecisionApplied`
- `RollbackApplied`
- `OpsCheckExecuted`

规划落点：

- `SQLiteEventStore`
- 默认数据库：`data/events.sqlite3`

## 3. 迁移开关

所有开关都保持“默认兼容 JSON 主路径”：

- `ZHIFEI_STORAGE_PRIMARY=json|sqlite`
- `ZHIFEI_STORAGE_ENABLE_SQLITE_MIRROR=false|true`
- `ZHIFEI_STORAGE_ENABLE_EVENT_LOG=false|true`
- `ZHIFEI_STORAGE_VALIDATE_DUAL_WRITE=false|true`
- `ZHIFEI_STORAGE_LEGACY_JSON_WRITE=false|true`
- `ZHIFEI_STORAGE_DB_PATH=/custom/path/metadata.sqlite3`
- `ZHIFEI_EVENT_DB_PATH=/custom/path/events.sqlite3`

默认值：

- 主读写仍为 `json`
- SQLite 镜像默认关闭
- 事件日志默认关闭
- 双写一致性校验默认关闭
- SQLite 主路径启用时，JSON 兼容写默认开启

## 4. 双写策略

### Phase 1

- 主路径：JSON
- 可选镜像：SQLite
- 事件：按开关写入
- 读路径：仍以 JSON 为准

### Phase 2

- 主路径：SQLite
- 兼容写：JSON
- 读路径：SQLite 优先，不存在时回退 JSON

### Phase 3

- 结构化读模型转向 SQLite / projection
- JSON 保留为兼容回退和历史快照来源

## 5. seed / replay / validate 命令

### 把现有 JSON 集合灌入 SQLite

```bash
python3 -m app.cli storage seed
python3 -m app.cli storage seed -c projects -c submissions
```

### 校验 JSON / SQLite 是否一致

```bash
python3 -m app.cli storage validate
python3 -m app.cli storage validate -c score_reports -c evidence_units
```

### 回放事件并构建项目活动 projection

```bash
python3 -m app.cli storage replay
python3 -m app.cli storage replay --persist
```

### 查看当前存储开关状态

```bash
python3 -m app.cli storage status
```

## 6. 数据校验策略

- 主校验方式：JSON 与 SQLite 的 canonical hash 对比
- 校验对象：每个已注册集合
- 校验粒度：集合级
- 失败行为：
  - 默认仅在 `storage validate` 中报告
  - 若显式开启 `ZHIFEI_STORAGE_VALIDATE_DUAL_WRITE=true`，写入后校验失败会直接抛错

## 7. 回退策略

- 立即回退：把 `ZHIFEI_STORAGE_PRIMARY` 设回 `json`
- 停止镜像：关闭 `ZHIFEI_STORAGE_ENABLE_SQLITE_MIRROR`
- 停止事件写入：关闭 `ZHIFEI_STORAGE_ENABLE_EVENT_LOG`
- JSON 历史快照仍保留，`/ops/versioned-json/.../rollback` 仍可用

## 8. 当前已知边界

- 业务层旧逻辑仍通过 `app/storage.py` 兼容门面访问存储，尚未全部改成显式注入 Repository。
- 原始资料上传链路刚开始通过 `ArtifactStore` 收口，目前已覆盖资料落盘主路径，尚未覆盖所有清理路径。
- `GovernanceDecisionApplied` 目前从应用服务层写事件；更细粒度的治理事件还可继续拆分。
