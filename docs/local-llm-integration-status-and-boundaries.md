# 评标系统本地大模型接入现状复盘与边界设计

本文档归档 Step 174B 对 clean worktree 的只读盘点结果，用于后续本地化大模型接入设计。本文档只记录现状、风险边界和推进顺序，不改变应用代码、配置、测试或 guard。

## 当前 Clean Worktree 状态

- 路径：`/Users/youfeini/Desktop/ZhiFei_BizSystem-local-llm-clean`
- 分支：`local-llm-integration-clean`
- HEAD：`2b28cdc58278e0ae165f234346e66d27e626f870`
- origin/main：`2b28cdc58278e0ae165f234346e66d27e626f870`
- HEAD...origin/main：`0 / 0`
- git status：`clean`
- 稳定标签：`v0.1.33-qingtian-main-worktree-untracked-inventory`

## 技术栈

当前仓库是 Python 项目，主要技术栈如下：

- Python 项目
- FastAPI + Uvicorn
- Streamlit Web UI
- Typer CLI
- Pydantic
- PyYAML
- python-docx
- PyMuPDF / pypdf
- OpenPyXL
- pillow / pytesseract
- Prometheus / Grafana
- pytest / ruff / black / pre-commit

## 已发现的 AI / LLM / Ollama / OpenAI / Spark / Gemini 文件

Step 174B 已发现以下 AI / LLM / Ollama / OpenAI / Spark / Gemini 相关文件和入口：

- `.env.example`
- `app/engine/llm_evolution.py`
- `app/engine/llm_evolution_common.py`
- `app/engine/llm_evolution_ollama.py`
- `app/engine/llm_evolution_openai.py`
- `app/engine/llm_evolution_spark.py`
- `app/engine/llm_judge_spark.py`
- `docs/ollama-minimal-integration-plan.md`
- `docs/ollama-runtime-usage.md`
- `app/main.py`

这些文件说明当前系统已有学习进化、Ollama preview、OpenAI、Spark、Gemini 等相关设计或实现线索。后续本地大模型接入必须先在这些入口周边做最小化设计，避免直接扩散到评分、存储和导出链路。

## 当前 LLM / Ollama 定位

当前系统已有 Ollama preview UI/API 文案。其边界已经明确：

- 不写入正式学习进化结果。
- 不影响评分。
- 不进入核心评分主链。
- 不应扩大到正式评分链。
- 不应扩大到真实评标写回。

因此，后续本地化大模型接入的第一阶段应继续保持 preview-only / mock-only / default-off，不直接进入 `score_text()`、`/rescore`、真实评标写回或持久化数据结果。

## 评标核心业务链路

当前评标核心业务链路包括：

- `app/engine/scorer.py`
- `app/engine/v2_scorer.py`
- `app/engine/dimensions.py`
- `app/engine/evidence.py`
- `app/engine/report_formatter.py`
- `app/engine/compare.py`
- `app/engine/learning.py`
- `app/engine/evolution.py`
- `app/main.py` 中项目、提交、评分、重评分、真实评标、证据追溯、评分依据、对比报告等 API/UI。

这些链路承担正式评分、证据、报告、学习和 UI/API 编排职责。任何让本地大模型影响这些结果的改动，都必须单独设计、单独验收，不应混入第一阶段 preview / helper 工作。

## 高风险链路

以下链路涉及数据保存、评分结果、写回、导出、运行产物或系统运行边界，属于高风险区域：

- `app/storage.py` 数据保存。
- `data/score_reports.json` 快照文档。
- `/rescore`。
- `ground_truth`。
- `qingtian-results`。
- `reports/latest`。
- `evidence_trace/latest`。
- `scoring_basis/latest`。
- DOCX / JSON / Markdown 导出。
- `scripts/e2e_api_flow.sh`。
- `scripts/export_*analysis_bundle.py`。
- `ops_agents` 后台脚本。
- 监控告警、认证、限流主逻辑。

第一阶段不得让本地模型写入上述链路，不得把 preview 结果升级为正式评分或正式学习进化输出。

## 测试 / Guard / CI 体系

当前仓库已存在测试、guard 和 CI 基础：

- `tests/` 下包含评分、主接口、存储、LLM evolution、Spark judge、DOCX export、runtime external data root、smoke guard、release guard 等测试。
- `pyproject.toml` 配置 pytest。
- `.github/workflows/ci.yml`。
- `.pre-commit-config.yaml`。
- `tools/smoke_guard.py`。
- `tools/release_guard.py`。
- `tests/test_smoke_guard.py`。
- `tests/test_release_guard.py`。
- ruff / black / pre-commit。

后续如果新增本地大模型 helper 或 default-off API bridge，应优先补 deterministic tests，再按需要补 `smoke_guard` / `release_guard` 边界检查。

## 后续本地化大模型接入最小前置文件清单

后续进入设计或实现前，建议最小前置文件清单如下：

- `.env.example`
- `docs/ollama-minimal-integration-plan.md`
- `docs/ollama-runtime-usage.md`
- `app/engine/llm_evolution_common.py`
- `app/engine/llm_evolution_ollama.py`
- `app/engine/llm_evolution.py`
- `app/engine/llm_evolution_openai.py`
- `app/main.py`
- `app/storage.py`
- `tools/smoke_guard.py`
- `tests/test_llm_evolution.py`
- `tests/test_main.py`
- `tests/test_smoke_guard.py`

该清单用于定位上下文，不等于授权修改。每一阶段仍需按任务边界决定允许修改的文件。

## 第一阶段明确不得接入的链路

第一阶段应坚持最小、可回退、默认关闭的原则，明确不得接入以下链路：

- 不接核心评分主链 `score_text();`
- 不接 `/rescore`。
- 不写 `app/storage.py` 数据结果。
- 不写仓库 `data/`。
- 不写 `output/`。
- 不运行真实 Ollama。
- 不改真实评标写回。
- 不接 `qingtian-results`。
- 不接 DOCX / 分析包导出。
- 不接 `ops_agents`。
- 不改监控告警。
- 不改认证 / 限流主逻辑。

这些限制用于防止本地大模型接入在早期阶段影响正式评分、正式数据、导出物或运行稳定性。

## 后续建议路线

建议后续按以下顺序推进：

1. docs-only 接入设计。
2. mock-only / preview-only helper。
3. default-off API bridge。
4. deterministic tests。
5. `smoke_guard` 场景。
6. guard / `release_guard` 边界补强。
7. 阶段复盘。
8. 稳定标签。

任何进入评分主链、存储链或导出链的能力，必须单独设计和验收，并明确数据写入、失败回退、开关默认值、权限边界和回归验证范围。
