# 本地模型 Preview / Mock 接入设计

本文档定义评标系统后续本地模型接入的 preview-only / mock-only / default-off 边界。本文档只描述设计，不实现接口、不修改代码、不启动本地模型、不改变正式评分、存储或导出链路。

## 阶段定位

当前阶段只做 preview-only / mock-only / default-off 接入设计。

- 不代表正式接入评分主链。
- 不代表生产启用本地模型。
- 不代表真实 Ollama 调用。
- 不代表可写入正式学习进化结果。
- 不代表可影响正式评标结果。

本阶段的目标是先把边界写清楚，确保后续任何实现都默认关闭、可测试、可回退，并且不进入核心评分主链。

## 当前基线

- worktree：`/Users/youfeini/Desktop/ZhiFei_BizSystem-local-llm-clean`
- branch：`local-llm-integration-clean`
- HEAD：`ab8ff7ccb3d4a3d617470ae6a12e7d7f2e82c197`
- remote branch：`origin/local-llm-integration-clean`
- tag：`v0.1.34-local-llm-integration-status-boundaries`
- git status：`clean`

## 现有相关能力

当前仓库已有以下本地模型或 LLM 相关线索：

- `.env.example` 中已有 `EVOLUTION_LLM_BACKEND=rules`。
- `.env.example` 中已有可选 `spark` / `openai` / `gemini` 后端说明。
- 已发现 `app/engine/llm_evolution_ollama.py`。
- 已发现 `docs/ollama-minimal-integration-plan.md`。
- 已发现 `docs/ollama-runtime-usage.md`。
- `app/main.py` 已有 Ollama preview UI/API 文案。
- 当前文案已明确“不写入正式学习进化结果、不影响评分、不进入核心评分主链”。

这些能力说明系统已经具备 LLM evolution 与 Ollama preview 的历史设计基础。后续接入应沿用 preview 边界，不把本地模型扩大为正式评分、正式写回或正式导出能力。

## 设计目标

本地模型在第一阶段只能作为 preview / mock / explain / advisory 能力。

设计目标如下：

- 不修改评分结果。
- 不写 `app/storage.py`。
- 不写 `data/`。
- 不写 `output/`。
- 不触发 DOCX / JSON / Markdown 导出。
- 不进入 `qingtian-results`。
- 不进入 `evidence_trace/latest`。
- 不进入 `scoring_basis/latest`。
- 不进入 `/rescore`。
- 不进入 `score_text()`。

第一阶段允许讨论 prompt/input preview、评分解释 preview、人工参考建议和 mock-only 输出结构，但不得产生任何正式结果或持久化副作用。

## Default-Off 策略

后续任何新 endpoint / helper / UI 入口都必须默认关闭。

- 必须通过 feature flag 显式启用。
- feature flag 未开启时必须处于 disabled 状态。
- disabled 时不得调用本地模型。
- disabled 时不得产生任何写入。
- disabled 时不得改变页面既有正式评分、报告、导出或写回行为。
- disabled 时应返回清晰的 preview/mock 状态，而不是静默执行。

建议后续命名保持语义明确，例如 `LOCAL_LLM_PREVIEW_ENABLED=false` 或等价 feature flag。具体变量名必须在实现阶段单独设计和验收。

## Preview-Only 响应要求

任何 preview-only 响应都必须显式表达其非正式性质。

响应建议包含：

- `preview_only=true`
- `mock_only=true` 或 `no_write=true`
- `disabled=true`，当 feature flag 未启用时返回
- 明确说明“不影响评分”
- 明确说明“不进入核心评分主链”
- 明确说明“不写入正式学习进化结果”

响应不得包含以下正式结果语义：

- `final_score`
- `write_result`
- `apply`
- `persist`
- `export`
- 任何暗示已写入、已应用、已生效或已导出的字段

preview-only 响应只能作为人工参考，不得成为正式评分、正式报告、正式写回或正式导出的输入。

## Mock-Only Helper 设计边界

mock-only helper 的边界必须比真实本地模型调用更严格。

允许范围：

- 构造本地模型输入预览。
- 生成 prompt/input preview。
- 生成 scoring explanation preview。
- 返回固定、可预测、deterministic 的 mock-only 响应。
- 返回 `preview_only=true` 与 `no_write=true`。

禁止范围：

- 不调用真实本地模型。
- 不调用 Ollama。
- 不写结果。
- 不读写 `data/`。
- 不读写 `output/`。
- 不调用 `score_text()`。
- 不调用 `app/storage.py` 写入。
- 不触发 DOCX / JSON / Markdown 导出。

mock-only helper 应优先用于测试接口契约、UI 状态和 guard 边界，不应承担真实模型推理职责。

## 本地 Ollama 调用边界

当前阶段不调用 Ollama。

后续如果需要真实调用 Ollama，只能在单独的 sandbox / preview endpoint 中实现，并必须满足以下条件：

- 必须受 feature flag 控制。
- 必须默认 disabled。
- 必须设置超时。
- 必须错误隔离。
- 必须不进入评分主链。
- 必须不写存储。
- 必须不写 `data/`。
- 必须不写 `output/`。
- 必须不触发导出。
- 必须不影响正式评分分数。

真实 Ollama 调用失败时，应只返回 preview 错误摘要或 fallback 说明，不得阻断正式评分、正式报告读取或正式业务流程。

## 高风险链路禁止接入

以下链路在第一阶段禁止接入本地模型：

- `score_text()`
- `/rescore`
- `app/storage.py`
- `data/`
- `output/`
- `qingtian-results`
- `reports/latest`
- `evidence_trace/latest`
- `scoring_basis/latest`
- DOCX / JSON / Markdown 导出
- `ground_truth`
- `ops_agents`
- `release_guard`
- `smoke_guard` runtime 正式门禁
- 监控告警、认证、限流主逻辑

这些链路涉及正式评分、数据保存、真实评标、导出交付、运行门禁或系统治理。任何接入都必须在后续单独设计、单独授权、单独验收。

## Deterministic Tests 设计思路

后续实现阶段应优先设计 deterministic tests，覆盖以下场景：

- disabled 状态。
- feature flag 开启后的 mock-only 响应。
- 不调用 Ollama。
- 不写 `data/`。
- 不写 `output/`。
- 不影响评分结果。
- 不调用 `score_text()`。
- 不调用 `app/storage.py` 写入。
- 响应不含正式结果字段。
- 响应包含 `preview_only=true`。
- 响应包含 `mock_only=true` 或 `no_write=true`。

测试应使用 monkeypatch / mock / fake helper 等方式证明无真实本地模型调用、无存储写入、无评分主链调用。

## Guard / Smoke Guard 设计边界

第一阶段只做 docs，不新增 guard 或 smoke_guard 行为。

后续如实现，必须先新增 guard task spec，并明确：

- guard 必须限制 allowed files。
- guard 必须禁止 `app/storage.py`。
- guard 必须禁止 `score_text()` 主链。
- guard 必须禁止 `data/`。
- guard 必须禁止 `output/`。
- guard 必须禁止导出脚本。
- guard 必须验证 preview-only / mock-only / default-off 边界。
- `smoke_guard` 不得直接进入 runtime 真实门禁，除非单独授权。
- `release_guard` 不得把 preview 能力误判为正式评分能力。

如果后续需要 smoke_guard 场景，应先做 mock-only 静态或合约场景，再单独评估是否需要 runtime 阶段。

## 后续推进路线

建议后续按以下步骤推进，每一步单独验收：

1. Step 174D：docs-only preview/mock 设计。
2. Step 174E：只读盘点现有 `llm_evolution` / `ollama` 文件。
3. Step 174F：mock-only helper 设计。
4. Step 174G：default-off API bridge 设计。
5. Step 174H：deterministic tests 设计。

在上述步骤完成前，不得将本地模型能力接入评分主链、存储链、导出链、真实评标写回或 runtime 正式门禁。
