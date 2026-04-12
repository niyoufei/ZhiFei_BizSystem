# `app/main.py` 热点审计

## 1. 事实基线

- 文件规模：`app/main.py` 约 `51985` 行
- 路由数量：AST 扫描得到 `172` 个路由
- 主要外部依赖入口：
  - `app.storage`：大量 `load_*` / `save_*`
  - `app.engine.*`：评分、证据、校准、演化、对比、运维 agent
  - `app.feedback_*` / `app.qingtian_dual_track` / `app.*_views`
- 顶层全局状态：
  - `app`
  - `router`
  - `compat_router`
  - 一整组材料解析缓存、队列、锁、worker 线程对象

## 2. 职责分类

### 2.1 启动装配

| 行号范围 | 事实 | 分类 |
| --- | --- | --- |
| `1-418` | 导入、`load_dotenv()`、引擎/存储/schema 装配、OpenAPI tags、常量 | 启动装配 |
| `21560-21618` | `_app_lifespan()`、`ensure_data_dirs()`、`prepare_secure_runtime()`、`validate_runtime_security_settings()`、`_start_material_parse_worker()`、`FastAPI(...)`、`configure_runtime_security()`、`configure_observability()` | 启动装配 |
| `35977-35978` | `app.include_router(router)`、`app.include_router(compat_router)` | 启动装配 |
| `51953-51985` | `create_app()` 与 `__main__` 启动 | 启动装配 |

### 2.2 接口注册

| 行号范围 | 事实 | 分类 |
| --- | --- | --- |
| `22079-22474` | `/health`、`/ready`、`/metrics`、`/system/self_check`、`/system/data_hygiene`、`/system/improvement_overview` | 接口注册 |
| `22532-35477` | 项目管理、材料、施组评分、证据追溯、校准、治理、学习、导出等主 API | 接口注册 |
| `35543-35972` | `/api/*` 兼容路由 | 接口注册 |
| `36024-36411` | `/web/*` 表单接口和首页 | 接口注册 |
| `36411-51952` | 首页内联 HTML/JS，负责前端交互逻辑拼装 | 接口注册 + 页面渲染 |

### 2.3 业务编排

下列区段已经不只是“路由层”，而是在 `main.py` 内直接承担应用服务职责：

| 行号范围 | 主要职责 | 证据 |
| --- | --- | --- |
| `899-7195` | 材料解析状态机、缓存、worker 调度、项目优先级、结果复用 | `_load_material_parse_*`、`_claim_next_material_parse_job`、`_complete_material_parse_job` |
| `7658-11527` | 上传材料归档、施组构建、项目上下文、评分输入构造、诊断聚合 | `_store_uploaded_material_from_local_path`、`_build_submission_record_from_local_path`、`_build_project_scoring_diagnostic` |
| `11528-16752` | 试车前体检、系统改进总览、评分依据、评分展示、闭环路径拼装 | `_build_project_trial_preflight`、`_build_system_improvement_overview`、`_build_phase1_closure_readiness` |
| `16753-21525` | 双轨评分、学习闭环、治理闭环、校准器治理、约束包重建 | `_build_submission_dual_track_summary`、`_refresh_project_reflection_objects`、`_sync_feedback_weights_to_evolution` |

### 2.4 存储调用

`main.py` 不是单纯“调 storage 一下”，而是直接按业务流程编排大量读写：

| 代表函数 | 行号 | 直接存储行为 |
| --- | --- | --- |
| `create_project` | `22532` | `ensure_data_dirs`、`load_projects`、`save_projects` |
| `create_project_from_tender` | `22615` | `load_projects`、`save_projects`、后续材料归档 |
| `_delete_project_cascade` | `23132` | 多数据集级联删除 |
| `upload_material` | `23636` | 材料入库、解析队列更新 |
| `score_text_for_project` | `30849` | `save_submissions`、`save_score_reports`、`save_evidence_units` |
| `_refresh_project_reflection_objects` | `18288` | `save_qingtian_results`、`save_delta_cases`、`save_calibration_samples` |
| `evolve_project` | `35000` | `save_evolution_reports` |

### 2.5 运维逻辑

| 行号范围 | 事实 |
| --- | --- |
| `11528-11586` | 聚合 `self_check`、`data_hygiene`、`evaluation_summary`、`ops_agents` 快照 |
| `21747-22077` | 数据卫生报告、自检上下文构造、系统自检执行 |
| `22426-22477` | 运维 API：`system_self_check`、`system_data_hygiene`、`system_improvement_overview` |
| `23975-24060` | 项目级 `trial_preflight` / markdown / docx 导出 |
| `32531-33490` | 第一阶段封关、系统总封关计算逻辑 |

### 2.6 学习闭环触发

| 行号范围 | 事实 |
| --- | --- |
| `18288-18383` | `_refresh_project_reflection_objects()` 刷新 DELTA_CASE / calibration sample |
| `18485-18651` | 由 delta case 或标签反馈自动更新权重 |
| `18652-18776` | 将反馈权重同步进 evolution report |
| `18691-19293` | 根据 ground truth 刷新 evolution report |
| `19294-20404` | 进化健康报告与治理快照回读 |
| `34667-35120` | `ground_truth` 录入与 `evolve_project()` 执行学习进化 |

## 3. 四类逻辑的边界混叠点

| 混叠点 | 行号 | 混叠的边界 | 为什么危险 |
| --- | --- | --- | --- |
| `_build_project_scoring_diagnostic` | `11403` | 评分核心 + 资料利用审计 + 项目 readiness | 改动这里会同时影响“分数解释”和“评分前置判断” |
| `_build_project_trial_preflight` | `11528` | 运维巡检 + 评分诊断 + 学习健康 | 已经不是纯 ops 报表，而是跨域总装配 |
| `_refresh_project_reflection_objects` | `18288` | 学习闭环 + 真实结果归一化 + 存储写回 | 这里一改错会污染训练样本和后续治理输入 |
| `_auto_update_project_weights_from_delta_cases` | `18594` | 学习闭环 + 专家权重治理 | 会影响后续项目级评分乘子 |
| `_build_governance_artifact_impacts` | `19996` | 治理闭环 + 历史版本快照 + 当前评分上下文 | 同时触碰历史回放和当前分数预览 |
| `_build_data_hygiene_report` | `21747` | 运维巡检 + 多数据集一致性 | 误修会导致治理和审计同时失真 |
| `score_text_for_project` | `30849` | 评分核心 + 存储持久化 + 证据单元写入 | 这是主链路裁决落点 |
| `add_ground_truth*` + `evolve_project` | `34667-35120` | 学习闭环 + 模型增强 + 治理审计 | 改动会影响真实结果驱动闭环 |

## 4. 直接文件 I/O 旁路

这些位置绕过了 `app.storage` 的统一 JSON/锁/快照约束：

| 行号 | 事实 |
| --- | --- |
| `2961-3035` | 手写上传暂存、复制、哈希计算、临时文件清理 |
| `11559-11583` | 直接读取 `build/ops_agents_status.json` / `build/ops_agents_history.json` |
| `28030`、`28052`、`28106` | 直接从文件路径读预览文本 |
| `33836-33855` | 直接备份并写回 `app/resources/lexicon.yaml`、`rubric.yaml` |

## 5. 最适合先拆的部分

先给事实：

- `main.py` 中最密集、最重复的编排集中在“项目/材料/施组上传评分”和“学习/治理刷新”两大片。
- 这些区域都已经具备相对完整的 helper 边界，具备抽成服务层的条件。

再给判断：

最适合先拆的三个模块是：

1. 材料接入与解析编排
   - 目标函数群：`_stage_upload_file_to_temp_path`、`_store_uploaded_material_from_local_path`、`_claim_next_material_parse_job`、`_complete_material_parse_job`
   - 原因：共享状态最重、和路由耦合最深、但对分数裁决不是最终裁决层
2. 项目级评分应用服务
   - 目标函数群：`upload_shigong`、`score_text_for_project`、`_score_submission_for_project`
   - 原因：主链路清晰，适合先抽“编排层”，保持引擎不动
3. 学习/治理编排服务
   - 目标函数群：`_refresh_project_reflection_objects`、`_auto_update_project_weights_from_delta_cases`、`_sync_feedback_weights_to_evolution`、`evolve_project`
   - 原因：边界已成片，但目前与 `main.py` 双向耦合过重

## 6. 一旦改错会直接破坏可解释性的区域

以下模块和函数应视为“解释性高风险区”，后续重构必须先加回放验证再动：

- `app.engine.scorer`
- `app.engine.v2_scorer`
- `app.engine.compare`
- `app.scoring_diagnostics`
- `app.submission_diagnostics`
- `app.engine.evidence_units`
- `app/main.py::_score_submission_for_project`
- `app/main.py::_build_project_scoring_diagnostic`
- `app/main.py::_resolve_project_scoring_context`
- `app/main.py::_resolve_project_ground_truth_score_rule`

原因很直接：

- 它们共同决定“总分、16 维度、证据、扣分项、建议、对比诊断”是否还能对上同一份输入。
- 这部分不能先做结构美化，再补验证；必须先固化回放样本，再做抽层。
