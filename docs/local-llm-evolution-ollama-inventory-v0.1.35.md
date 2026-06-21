# LLM Evolution / Ollama 只读盘点复盘 v0.1.35

本文档归档 Step 174E 对 `llm_evolution` / Ollama / OpenAI / Spark / Gemini 相关文件的只读盘点结果，用于后续 mock-only helper 与 default-off preview API bridge 设计。本文档只记录现状与边界，不代表实现本地模型接入。

## 阶段定位

- 本文档归档 Step 174E 只读盘点结果。
- 只做现状记录，不代表实现本地模型接入。
- 本阶段不启动服务。
- 本阶段不运行 Ollama。
- 本阶段不运行测试。
- 本阶段不修改代码。

## 当前基线

- worktree：`/Users/youfeini/Desktop/ZhiFei_BizSystem-local-llm-clean`
- branch：`local-llm-integration-clean`
- HEAD：`aad9fa945c28d099df1429609b1db65693af4ef6`
- remote branch：`origin/local-llm-integration-clean`
- tag：`v0.1.35-local-llm-preview-mock-integration-design`
- git status：`clean`

## llm_evolution_common.py 摘要

`app/engine/llm_evolution_common.py` 是 LLM evolution 的共享 prompt 与响应解析层，主要对象包括：

- `EVOLUTION_PROMPT`：统一进化增强 prompt。
- `build_evolution_prompt()`：从规则版报告、真实评标记录、项目上下文构造 prompt。
- `parse_evolution_response()`：解析并校验 `high_score_logic` / `writing_guidance` 字符串数组。

该文件适合作为 prompt / response 结构复用参考，但不得直接扩大为写入链，也不得让其承担存储、评分或导出职责。

## llm_evolution.py 摘要

`app/engine/llm_evolution.py` 是进化 LLM 后端选择与手动 Ollama preview 包装层，主要对象包括：

- `EVOLUTION_LLM_BACKEND_ENV = "EVOLUTION_LLM_BACKEND"`。
- `get_evolution_llm_backend()`：默认返回 `rules`。
- `get_llm_backend_status()`：返回 spark / openai / gemini / ollama 配置状态，且不暴露密钥。
- `enhance_evolution_report_with_llm()`：按 backend 分发到 spark / openai / gemini / ollama，失败返回 `None`。
- `preview_evolution_report_with_ollama()`：手动 Ollama preview 入口，不持久化，失败返回规则版 preview 与 fallback 信息。

该文件可作为 preview-only / mock-only 调度语义参考，但第一阶段不得把其扩展为评分主链或存储链入口。

## llm_evolution_ollama.py 摘要

`app/engine/llm_evolution_ollama.py` 是 Ollama HTTP 调用封装层，主要对象包括：

- `OLLAMA_DEFAULT_BASE_URL = "http://localhost:11434"`。
- `_get_ollama_base_url()` / `_get_ollama_model()`。
- `_build_ollama_chat_url()`。
- `_extract_json_from_content()`。
- `_call_ollama_http()`：使用 `urllib.request.urlopen` 真实调用 Ollama `/api/chat`。
- `enhance_evolution_report_ollama()`：未配置 `OLLAMA_MODEL` 时返回 `None`，成功时返回 `enhanced_by="ollama"` 的进化报告片段。

该文件存在真实本地网络调用能力。当前阶段不得运行该调用；后续如需真实调用，必须另行 sandbox / preview-only 设计。

## OpenAI / Spark / Gemini 相关摘要

- `app/engine/llm_evolution_openai.py` 读取 `OPENAI_API_KEY` / `OPENAI_MODEL`，并通过 `_call_openai_http()` 调用 OpenAI Chat Completions。
- `app/engine/llm_evolution_spark.py` 复用 `app.engine.llm_judge_spark._call_spark_http()`。
- `app/engine/llm_evolution_gemini.py` 存在 `urllib.request` 调用路径。
- `app/engine/llm_judge_spark.py` 包含 Spark HTTP URL、模型/token 读取、payload 构造、JSON 校验和后处理。

这些文件属于真实 LLM 后端能力边界。第一阶段只可作为只读参考，不得触发网络调用。

## 真实网络调用边界

以下文件存在真实网络调用路径：

- `app/engine/llm_evolution_ollama.py`
- `app/engine/llm_evolution_openai.py`
- `app/engine/llm_evolution_gemini.py`
- `app/engine/llm_judge_spark.py`

当前阶段不得运行这些调用。后续如需真实调用，必须另行 sandbox / preview-only 设计，并明确 feature flag、超时、错误隔离、不写入和不接核心评分主链。

## 文件写入 / Storage 写入边界

Step 174E 盘点结论：

- `llm_evolution*.py` 未发现 storage 写入。
- `app/main.py` 中存在正式写入链路，包括：
  - `save_evolution_reports`
  - `save_score_reports`
  - `save_qingtian_results`
  - `save_submissions`
- 第一阶段不得写入 `app/storage.py`。
- 第一阶段不得写入 `data/`。
- 第一阶段不得写入 `output/`。

后续 mock-only helper 应保持无写入；如需 API bridge，也必须 default-off 且在 disabled 状态下不产生任何写入。

## 评分主链 / 高风险入口

Step 174E 盘点结论：

- `llm_evolution*.py` 未直接调用 `score_text`。
- `llm_evolution*.py` 未直接调用 `/rescore`。
- `llm_evolution*.py` 未直接调用 `qingtian-results`。
- `llm_evolution*.py` 未直接调用 `evidence_trace/latest`。
- `llm_evolution*.py` 未直接调用 `scoring_basis/latest`。
- `app/main.py` 中这些均为高风险正式业务入口。
- Ollama preview 入口与这些链路并列存在，但必须保持不调用评分主链和存储写入。

这些边界必须继续保持，避免本地模型能力影响正式评分、正式报告、真实评标写回或交付导出结果。

## app/main.py 相关入口摘要

Step 174E 只读检索发现：

- `app/main.py` 导入 `preview_evolution_report_with_ollama`。
- `app/main.py` 导入 `enhance_evolution_report_with_llm`。
- `app/main.py` 导入 `score_text` / `score_text_v2`。
- 存在 `POST /api/v1/projects/{project_id}/evolve/ollama_preview`。
- 前端存在 `btnOllamaPreview`。
- 页面文案明确不重新评分、不触发 `rescore`、不写 `data`、不接核心评分主链。

后续 default-off preview API bridge 如需实现，应优先保持现有 preview 语义，不接正式评分、写回、导出和运行门禁。

## tests/test_llm_evolution.py 覆盖摘要

`tests/test_llm_evolution.py` 已覆盖：

- 默认 backend 为 `rules`。
- spark / openai / gemini / ollama backend 读取。
- Ollama status 由 `OLLAMA_MODEL` 判断。
- 无凭据 / 无模型时返回 `None`。
- mocked Ollama success。
- Ollama backend 失败重试一次。
- `preview_evolution_report_with_ollama()` 成功、失败、异常 fallback。
- `parse_evolution_response()` 基础校验。
- 测试中 `urllib.request.urlopen` 被 patch，不需要真实网络。

后续 deterministic tests 应继续沿用 mock / patch 方式，证明不调用真实 Ollama、不写存储、不进入评分主链。

## 后续 Mock-Only Helper 最小建议文件清单

优先新增：

- `app/engine/local_llm_preview_mock.py`
- `tests/test_local_llm_preview_mock.py`

可只读参考但不优先修改：

- `app/engine/llm_evolution_common.py`
- `app/engine/llm_evolution.py`
- `tests/test_llm_evolution.py`
- `docs/local-llm-preview-mock-integration-design.md`

mock-only helper 应只构造本地模型输入预览、prompt preview 或 scoring explanation preview，不调用真实本地模型，不读写 `data/` / `output/`。

## 后续 Default-Off Preview API Bridge 最小建议文件清单

仅设计阶段可参考：

- `app/main.py`
- `tests/test_main.py`
- `.env.example`
- `docs/local-llm-preview-mock-integration-design.md`
- `tools/smoke_guard.py`

`.env.example` 和 `tools/smoke_guard.py` 只能在明确实现阶段授权后修改。API bridge 必须 default-off，feature flag 未启用时必须 disabled，且不得产生任何写入。

## 第一阶段不得修改的文件

第一阶段不得修改：

- `app/storage.py`
- `app/engine/scorer.py`
- `app/engine/v2_scorer.py`
- `app/engine/evidence.py`
- `app/engine/report_formatter.py`
- `tools/release_guard.py`
- `scripts/e2e_api_flow.sh`
- `scripts/export_*analysis_bundle.py`
- `data/`
- `output/`

这些文件和目录涉及正式评分、存储、证据、报告、导出、运行验证或运行产物，必须保持隔离。

## 第一阶段不得接入的链路

第一阶段不得接入：

- `/rescore`
- `qingtian-results`
- `evidence_trace/latest`
- `scoring_basis/latest`
- DOCX / JSON / Markdown 正式导出
- `ops_agents`
- 评分主链
- 存储链
- 导出链
- 真实评标写回
- runtime 正式门禁

任何进入上述链路的能力，都必须单独设计、单独授权、单独验收。

## 后续推进建议

建议后续推进顺序：

1. 先做 mock-only helper 设计。
2. 再做 helper-only 实现。
3. 再做 deterministic tests。
4. 再做 default-off API bridge 设计。
5. 最后才评估 preview-only API bridge 实现。

后续不得直接接评分主链、存储链、导出链或真实评标写回。每一步必须单独验收，并保留 default-off / preview-only / mock-only 边界。
