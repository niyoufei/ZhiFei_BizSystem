# 存储热点审计

## 1. 单一真理源现状

事实：

- 当前持久化主路径是 `app/storage.py`
- 主要数据源仍是本地 JSON 文件
- 关键路径常量集中在 `app/storage.py:43-69`

核心 JSON 产物包括：

- `projects.json`
- `submissions.json`
- `materials.json`
- `ground_truth_scores.json`
- `evolution_reports.json`
- `expert_profiles.json`
- `score_reports.json`
- `project_anchors.json`
- `project_requirements.json`
- `evidence_units.json`
- `qingtian_results.json`
- `calibration_models.json`
- `delta_cases.json`
- `calibration_samples.json`
- `patch_packages.json`
- `patch_deployments.json`
- `high_score_features.json`
- `material_parse_jobs.json`

## 2. 文件锁、原子写、历史快照、DPAPI 入口

### 2.1 文件锁

| 位置 | 事实 |
| --- | --- |
| `app/storage.py:94-104` | `_get_path_lock()` 维护进程内 `_PATH_LOCKS` 字典 |
| `app/storage.py:107-127` | `_exclusive_file_lock()` 为每个目标文件创建 `.lock` 并在 POSIX 上使用 `fcntl.flock` |

### 2.2 原子写

| 位置 | 事实 |
| --- | --- |
| `app/storage.py:148-168` | `_atomic_write_bytes()` 使用 `tempfile.mkstemp` + `os.replace` + 父目录 `fsync` |
| `app/storage.py:172-173` | `_atomic_write_text()` 只是 `_atomic_write_bytes()` 的文本封装 |
| `app/storage.py:334-340` | `save_bytes()` 在锁保护下执行原子写 |
| `app/storage.py:407-421` | `save_json()` 统一 JSON 序列化与异常包装 |

### 2.3 历史快照

| 位置 | 事实 |
| --- | --- |
| `app/storage.py:243-261` | `_snapshot_path_for()` / `_version_id_from_name()` 定义版本快照路径 |
| `app/storage.py:264-286` | `list_json_versions()` 枚举快照 |
| `app/storage.py:288-295` | `_write_json_version_snapshot()` 创建快照 |
| `app/storage.py:297-320` | `restore_json_version()` 回滚并给当前版本再做备份 |
| `app/storage.py:388-405` | `load_json_version()` 读取指定历史版本 |

### 2.4 DPAPI 加密入口

| 位置 | 事实 |
| --- | --- |
| `app/storage.py:17-20` | `_SECURE_FILE_MAGIC`、`_SECURE_RUNTIME_LOCK`、`_DPAPI_OPTIONAL_ENTROPY` |
| `app/storage.py:175-229` | `_require_windows_dpapi()`、`_dpapi_crypt()`、`_encrypt_payload()`、`_decrypt_payload()` |
| `app/storage.py:439-460` | `prepare_secure_runtime()` 在安全桌面模式下将现有数据文件迁移为加密存储 |
| `app/main.py:21560-21566` | `_app_lifespan()` 启动时调用 `prepare_secure_runtime()` |
| `app/windows_desktop.py:12-24` | 设置 `ZHIFEI_SECURE_DESKTOP` / `ZHIFEI_DATA_DIR`，触发加密路径 |

## 3. 自动保留历史版本的数据集

`save_json(..., keep_history=True)` 目前只用于以下四类治理/学习核心数据：

- `app/storage.py:549` `save_evolution_reports`
- `app/storage.py:558` `save_expert_profiles`
- `app/storage.py:612` `save_calibration_models`
- `app/storage.py:657` `save_high_score_features`

这四类数据同时被治理回滚与评分上下文读取使用，属于高风险区。

## 4. 历史版本读取/回滚入口

### 4.1 API 入口

| 位置 | 事实 |
| --- | --- |
| `app/main.py:23090` | `/ops/versioned-json/{artifact}` 查看版本历史 |
| `app/main.py:23115` | `/ops/versioned-json/{artifact}/rollback` 回滚 |

### 4.2 治理闭环入口

| 位置 | 事实 |
| --- | --- |
| `app/main.py:19880-20040` | `_load_latest_valid_governance_snapshot()` 与 `_build_governance_artifact_impacts()` 会回读历史快照用于治理预览 |
| `app/feedback_governance.py:261`、`578`、`589` | 通过 `main.list_json_versions()` / `main.load_json_version()` 进入版本治理逻辑 |

## 5. 绕过 `app.storage` 的直接文件读写

这些位置不是“坏代码”，但它们确实绕开了 `storage.py` 的锁、快照、统一异常包装。

### 5.1 `app/main.py`

| 位置 | 事实 | 风险 |
| --- | --- | --- |
| `2961-3035` | 手写文件复制、哈希与临时文件 staging | 不走统一锁/审计 |
| `11559-11583` | 直接读取 `build/ops_agents_status.json`、`build/ops_agents_history.json` | 运维快照未纳入统一存储协议 |
| `28030`、`28052`、`28106` | 直接按路径读取上传文件预览文本 | 文本预览与正式解析协议分离 |
| `33836-33855` | 直接备份并写回 `app/resources/lexicon.yaml`、`rubric.yaml` | 修改评分规则配置，不经过 JSON 版本治理 |

### 5.2 `app/cli.py`

| 位置 | 事实 |
| --- | --- |
| `46` | 直接 `read_text()` 读输入 |
| `193`、`199`、`257`、`525` | 直接 `write_text()` 写 JSON / summary / batch 汇总 |

### 5.3 `app/web_ui.py`

| 位置 | 事实 |
| --- | --- |
| `197-199` | 通过 `NamedTemporaryFile` 暂存 DOCX 再回读 bytes |

### 5.4 其他模块

| 模块 | 事实 |
| --- | --- |
| `app/app.py` | 旧版 CSV 存储 |
| `app/config.py` | 直接读取配置文件 |
| `app/i18n.py` | 直接读取 locale yaml |
| `app/engine/anchors.py` | 直接读取 `BASE_REQUIREMENT_PACK_PATH` |
| `app/engine/template_rag.py` | 直接读取 `high_score_probe_dimensions.json` |
| `app/engine/feature_distillation.py` | 直接读取 bootstrap JSON |
| `app/engine/evidence_units.py` | 模块级维度元信息缓存直接读 JSON |
| `app/engine/llm_judge_spark.py` | 直接读 prompt 文本 |

## 6. 存储热点判断

先给事实：

- 评分结果、证据单元、真实评标、校准模型、进化报告都还在 JSON 文件路径上。
- 历史快照只覆盖了部分治理产物，没有覆盖所有高价值文本配置。
- `lexicon.yaml` / `rubric.yaml` 的在线写回不经过 `storage.py`。

再给判断：

- 若后续要引入存储适配层，第一优先级不是“换数据库”，而是先把所有高价值写操作收拢到统一仓储接口。
- 对本系统而言，最危险的不是 JSON 本身，而是“同一个系统同时存在统一存储协议和旁路写入协议”。
