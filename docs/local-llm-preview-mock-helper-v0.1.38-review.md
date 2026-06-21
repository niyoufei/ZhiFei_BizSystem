# 本地模型 Mock-Only Helper v0.1.38 实现复盘

本文档记录 `v0.1.38-local-llm-preview-mock-helper` 阶段的 mock-only helper 第一版实现范围、测试结果、未接入链路、风险边界和后续推进建议。

## 阶段定位

本文档记录 v0.1.38 mock-only helper 第一版实现。

- 该阶段只实现纯函数 helper 与 deterministic tests。
- 不代表 API 接入。
- 不代表 UI 接入。
- 不代表真实模型调用。
- 不代表生产启用本地模型。
- 不代表评分主链、存储链或导出链接入。

## 当前基线

- worktree：`/Users/youfeini/Desktop/ZhiFei_BizSystem-local-llm-clean`
- branch：`local-llm-integration-clean`
- HEAD：`d9c783e2eb7f5be71746fe38c9c6cfa3b5bb45a1`
- tag：`v0.1.38-local-llm-preview-mock-helper`

## 本阶段新增文件

- `app/engine/local_llm_preview_mock.py`
- `tests/test_local_llm_preview_mock.py`

## 已实现函数

- `validate_local_llm_preview_boundary(payload: dict) -> None`
- `build_local_llm_preview_input(payload: dict) -> dict`
- `build_local_llm_mock_response(preview_input: dict) -> dict`

## 已实现能力

- 校验 payload 为 `dict`。
- 校验必填字段。
- 校验 `mode` 仅允许 `mock_only` / `preview_only`。
- 递归拒绝 forbidden exact keys。
- 构造 deterministic `preview_input`。
- 构造 deterministic mock advisory response。
- 输出包含 `mode=mock_only`、`preview_only=true`、`no_write=true`、`affects_score=false`、`source=local_llm_preview_mock`。
- 不修改输入对象。

## 禁止字段

helper 会拒绝输入任意层级出现以下 forbidden exact keys，输出也不得包含这些正式结果、写入、应用、导出或真实模型语义：

- `final_score`
- `score_result`
- `write_result`
- `persist`
- `export`
- `apply`
- `rescore`
- `qingtian_results`
- `evidence_trace_write`
- `scoring_basis_write`
- `storage_write`
- `score_text`
- `ollama`
- `openai`
- `spark`
- `gemini`

## 测试结果

执行命令：

```bash
python -m pytest tests/test_local_llm_preview_mock.py
```

结果：

```text
10 passed in 0.02s
```

## 测试覆盖

- valid payload 构造 preview input。
- valid preview input 构造 mock response。
- 输出边界字段。
- 输入对象不被修改。
- 缺少必填字段报错。
- 非 `dict` 输入报错。
- 非法 `mode` 报错。
- forbidden top-level key 报错。
- forbidden nested key 报错。
- 输出不包含 forbidden exact keys。
- 源码静态断言不包含 storage / scorer / real model / urllib / requests / httpx / subprocess / open / Path。

## 明确未接入

本阶段未接入以下文件、模块或链路：

- `app/main.py`
- `app/storage.py`
- `app/engine/scorer.py`
- `app/engine/v2_scorer.py`
- `app/engine/llm_evolution_ollama.py`
- `app/engine/llm_evolution_openai.py`
- `app/engine/llm_evolution_spark.py`
- `app/engine/llm_evolution_gemini.py`
- `.env.example`
- `tools/smoke_guard.py`
- `tools/release_guard.py`
- API
- UI
- `data/`
- `output/`
- `qingtian-results`
- `evidence_trace`
- `scoring_basis`
- DOCX / JSON / Markdown 正式导出

## 边界确认

- 未调用真实模型。
- 未启动服务。
- 未运行 Ollama。
- 未访问网络。
- 未写 `data/` / `output/` / storage。
- 未触发 `score_text` / `rescore`。
- 未接 API。
- 未接 UI。
- 未接 `qingtian-results`、`evidence_trace`、`scoring_basis`。
- 未接 DOCX / JSON / Markdown 正式导出。

## 风险边界

当前 helper 只提供 deterministic preview/mock 结构。它不应被解释为评分依据、正式结果、写回结果、导出结果或真实模型输出。

后续如果要把 helper 暴露给 API 或 UI，必须继续保持 default-off、preview-only、no-write 边界，并新增针对 allowed files、禁用链路、feature flag 和响应字段的 guard 约束。

## 后续推进建议

建议下一阶段顺序：

1. 先做 default-off API bridge 设计文档。
2. 再做 guard task spec 设计。
3. 再考虑 API bridge 实现。
4. 不得直接接 UI。
5. 不得直接接真实 Ollama。
6. 不得直接接评分主链或存储链。

任何进入 API、UI、真实模型调用、评分主链、存储链或导出链的能力，都必须单独设计、单独验收、单独打标签。
