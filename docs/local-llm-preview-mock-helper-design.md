# 本地模型 Mock-Only Helper 设计

本文档定义后续本地模型 mock-only helper 的目标、输入输出、禁止链路、测试边界和最小实现范围。本文档只做设计，不实现 helper 代码，不新增测试代码，不接 API / UI / 模型运行时。

## 阶段定位

当前阶段只设计 mock-only helper。

- 不实现代码。
- 不调用真实 Ollama / OpenAI / Spark / Gemini。
- 不进入评分主链。
- 不写任何数据。
- 不写 `app/storage.py`。
- 不读写 `data/`。
- 不读写 `output/`。

该 helper 的后续实现目标是提供 deterministic、无副作用、可测试的本地模型输入预览和 advisory mock 结构，不产生正式评分或正式写回语义。

## 当前基线

- worktree：`/Users/youfeini/Desktop/ZhiFei_BizSystem-local-llm-clean`
- branch：`local-llm-integration-clean`
- HEAD：`79002818d429fb0434b8791499544873474b0e63`
- remote branch：`origin/local-llm-integration-clean`
- tag：`v0.1.36-local-llm-evolution-ollama-inventory`
- git status：`clean`

## 建议后续 Helper 文件

后续如进入 helper-only 实现阶段，建议新增：

- `app/engine/local_llm_preview_mock.py`
- `tests/test_local_llm_preview_mock.py`

本阶段不新增上述文件，只记录设计边界。

## Helper 设计目标

mock-only helper 的目标是：

- 构造本地模型输入预览。
- 构造评分解释 preview。
- 构造 LLM advisory response mock。
- 返回结构化 preview payload。
- 不调用真实模型。
- 不写入 storage。
- 不改变评分结果。
- 不进入 `score_text()`。
- 不进入 `/rescore`。

该 helper 应作为纯函数工具，不承担网络调用、存储写入、评分计算、报告导出或真实评标写回职责。

## 建议函数草案

后续 helper-only 实现可考虑以下函数草案：

```python
def build_local_llm_preview_input(payload: dict) -> dict:
    ...


def build_local_llm_mock_response(preview_input: dict) -> dict:
    ...


def validate_local_llm_preview_boundary(payload: dict) -> None:
    ...
```

函数职责建议：

- `build_local_llm_preview_input()`：规范化输入，截断或整理文本片段，构造 preview input。
- `build_local_llm_mock_response()`：基于 preview input 返回 deterministic advisory mock payload。
- `validate_local_llm_preview_boundary()`：拒绝 forbidden fields，校验必填字段和 no-write 边界。

## 输入字段建议

建议输入 payload 包含：

- `project_id`
- `submission_id`
- `text_excerpt`
- `scoring_context`
- `evidence_context`
- `requirement_hits`
- `mode`
- `requested_by`

实现阶段应明确必填字段、可选字段、最大长度、默认值和错误消息。输入对象不得被原地修改。

## 输出字段要求

输出必须包含：

- `mode: "mock_only"`
- `preview_only: true`
- `no_write: true`
- `affects_score: false`
- `source: "local_llm_preview_mock"`
- `preview_input`
- `advisory`

其中 `advisory` 只能表达人工参考建议，不得表达正式评分、正式写回或正式导出结果。

## 输出禁止字段

输出不得包含：

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

这些字段属于正式结果、写入、应用、导出或高风险链路语义。mock-only helper 必须拒绝或避免生成这些字段。

## 禁止调用链路

mock-only helper 禁止调用：

- Ollama
- OpenAI
- Spark
- Gemini
- `score_text()`
- `/rescore`
- `app/storage.py` 写入
- `data/`
- `output/`
- `qingtian-results`
- `evidence_trace/latest` 写入
- `scoring_basis/latest` 写入
- DOCX / JSON / Markdown 导出
- `ops_agents`
- `smoke_guard` runtime 正式门禁

helper 应保持纯函数属性，不导入真实 LLM 调用模块，不调用网络，不写文件。

## Deterministic Tests 设计

后续测试建议覆盖：

- valid payload 返回 `mock_only` / `preview_only` / `no_write`。
- forbidden fields 报错。
- 缺少必填字段报错。
- 输出不包含 forbidden fields。
- 输入对象不被修改。
- 不调用任何网络。
- 不写任何文件。
- 不调用 `score_text()`。
- 不调用 storage。
- 不读写 `data/` / `output/`。

测试应使用 monkeypatch / mock 防线证明无网络、无写入、无评分主链调用。测试不应启动服务，不应运行 Ollama。

## 风险控制

helper 实现必须满足：

- helper 仅纯函数。
- 不导入 `app/main.py`。
- 不导入 `app/storage.py`。
- 不导入 `app/engine/llm_evolution_ollama.py`。
- 不导入 OpenAI / Spark / Gemini 真实调用模块。
- 不导入会触发网络或写入副作用的模块。
- 后续若需真实调用，必须另行 sandbox 设计。

如果实现阶段发现必须访问项目状态、提交状态或评分报告，应停止并重新设计，不得把 mock-only helper 扩大为业务写入或评分计算入口。

## 后续实现准入条件

进入实现前必须满足：

1. 先完成 docs 复核。
2. 再做 helper-only 实现。
3. 再做 deterministic tests。
4. 再做 guard task spec。
5. 再做 smoke checks。
6. 不得一次性接 API / UI / 模型运行时。

任何扩展到 API bridge、UI、真实模型调用、评分主链、存储链或导出链的需求，都必须独立拆分、独立验收。

## 第一阶段保持禁止的范围

第一阶段继续禁止：

- 修改 `app/main.py`。
- 修改 `app/storage.py`。
- 修改 `app/engine/scorer.py`。
- 修改 `app/engine/v2_scorer.py`。
- 修改 `.env.example`。
- 修改 `tools/smoke_guard.py` / `tools/release_guard.py`。
- 接入真实 Ollama / OpenAI / Spark / Gemini。
- 写 `data/`。
- 写 `output/`。
- 触发 DOCX / JSON / Markdown 正式导出。
- 进入 `qingtian-results`。
- 进入 `evidence_trace/latest` 写入。
- 进入 `scoring_basis/latest` 写入。

mock-only helper 的价值在于先建立可验证的 no-write、no-network、no-score 边界，再决定是否进入下一阶段。
