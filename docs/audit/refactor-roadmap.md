# 分阶段重构路线图

## 1. 约束前提

本路线图遵守以下前提：

- 不做大爆炸重构
- 不把系统改成 AI 黑盒评分器
- 规则引擎保留最终裁决权
- 保留 JSON 文件存储兼容路径
- Web/API、CLI、Windows 安全桌面最终必须共用同一应用服务层
- 任何影响分数的改动都必须有回放验证和前后差异报告

## 2. 本次审计后的判断

先给事实：

- 现有测试与 smoke 已足够建立当前行为基线
- 当前真正的问题不是“评分内核不工作”，而是“编排层、边界层、运维层混在一起”

再给判断：

- 第一阶段不应该先拆评分内核
- 第一阶段应该先把 `app.main` 中的编排层剥离出来

## 3. P0 路线

### P0-1 修复运行基线，而不是改业务

目标：

- 先让 `make run`、`make test` 回到可用

动作：

1. 修复或重建仓库内 `.venv`
2. 保证 `Makefile` 选择的解释器与真实可运行环境一致
3. 把本次审计用到的启动/测试命令固化到基线说明

为什么是 P0：

- 当前默认命令面已经失真，不修复会持续误导后续重构验证

回滚：

- 不动业务代码，只回到当前系统 `python3` 直启方式

### P0-2 建立应用服务层壳，不改引擎行为

目标：

- 把 `app.main` 从“业务总控文件”先降级成“路由 + 装配入口”

建议先抽的服务：

1. `app/services/project_intake_service.py`
2. `app/services/material_ingest_service.py`
3. `app/services/submission_scoring_service.py`

迁移方式：

1. 先复制编排逻辑到服务层，保留原 helper 名称与返回协议
2. `app.main` 路由改为调服务层
3. CLI 和 `web_ui.py` 逐步复用同一服务层

为什么这样拆：

- 这三块是主链路入口，收益高
- 可以不碰评分引擎内部规则

完成定义：

- API/CLI 返回结构不变
- 当前 smoke / pytest / 关键 API 回放结果不变

### P0-3 固化解释性回放基线

目标：

- 把“同输入 -> 同分数/同证据/同建议/同诊断”的行为固定下来

动作：

1. 建立项目级回放样本集
2. 对以下产物做差异对比：
   - `total_score`
   - `dimension_scores`
   - `penalties`
   - `suggestions`
   - `evidence_trace`
   - `scoring_basis`
3. 输出前后差异报告

为什么是 P0：

- 没有回放基线，后续任何重构都无法证明没破坏可解释性

## 4. P1 路线

### P1-1 抽离学习闭环应用服务

目标：

- 把 ground truth、delta case、calibration sample、evolution report 的编排从 `main.py` 中移出

建议服务：

1. `app/services/ground_truth_service.py`
2. `app/services/reflection_service.py`
3. `app/services/evolution_service.py`

边界要求：

- 模型增强只返回候选文本，不直接裁决最终分数
- 样本刷新、模型训练、进化报告保存必须全量留痕

### P1-2 抽离治理闭环

目标：

- 把版本快照、治理预览、回滚、人工确认审计隔离成单独服务

建议服务：

1. `app/services/governance_service.py`
2. `app/services/versioned_artifact_service.py`

原因：

- 当前治理逻辑既读当前值又读历史快照，还参与评分上下文预览，风险高但边界清晰

### P1-3 抽离运维巡检聚合层

目标：

- 让 `self_check`、`data_hygiene`、`trial_preflight`、`system_improvement_overview` 成为清晰的 ops aggregation layer

建议服务：

1. `app/services/system_health_service.py`
2. `app/services/preflight_service.py`
3. `app/services/system_closure_service.py`

原因：

- 这一层本质是“聚合诊断”，不应继续和评分编排互相嵌套

## 5. P2 路线

### P2-1 建立事件化内核，但仍保持分层单体

目标：

- 不是上微服务，而是在单体内建立受控事件流

建议事件：

- `project.created`
- `material.ingested`
- `material.parsed`
- `submission.scored`
- `ground_truth.recorded`
- `reflection.refreshed`
- `calibrator.trained`
- `evolution.report_saved`
- `governance.rollback_applied`

落地方式：

1. 先写本地事件 journal
2. 与现有 JSON 双写
3. 只做审计、回放、异步诊断，不接管最终裁决

### P2-2 存储适配层

目标：

- 保留 JSON 为 SSOT，同时让上层不再直接知道具体文件路径

做法：

1. 抽 repository / gateway 接口
2. 先让现有 `app.storage` 充当默认适配器
3. 若未来需要只读索引层，再在不改主路径的情况下附加 read model

### P2-3 页面渲染从 `main.py` 中迁出

目标：

- 把内联 HTML/JS 从主编排文件中移出

原因：

- 这会显著降低 `main.py` 的改动噪声
- 但它不是裁决风险最高的第一优先级，因此排在 P2

## 6. 暂不建议先做的事

- 直接重写 `app.engine.scorer`
- 直接替换 `app.engine.v2_scorer`
- 直接引入数据库并废弃 JSON
- 直接把系统拆成多个进程或微服务
- 让 agent 直接写最终权重、最终分数、生产配置

原因：

- 这些动作都先于“边界清理”和“回放验证”发生，会把系统从可解释评分器推向不可回滚状态

## 7. 本轮审计后的优先级结论

### P0

1. 修复 `.venv` / `Makefile` 基线
2. 抽 `project/material/submission` 三个应用服务壳
3. 固化解释性回放与差异报告

### P1

1. 抽学习闭环服务
2. 抽治理闭环服务
3. 抽运维巡检聚合层

### P2

1. 单体内事件化 journal
2. 存储适配层
3. 页面渲染拆离 `main.py`

## 8. 本轮是否需要新增 smoke tests

结论：暂不新增。

原因：

- 当前已有 `1549` 个测试和 `scripts/smoke_test.sh`
- 本轮是审计，不是行为改造
- 现有基线已足够支撑下一阶段的“服务壳抽离”

后续一旦进入 P0-2 实施，再补“服务层回放 smoke”最合适
