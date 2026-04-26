# 《ZhiFei_BizSystem Ollama 可选 backend 使用说明》

本文档说明如何在不改变默认评分行为的前提下，按需启用本机 Ollama 作为学习进化增强后端。

## 一、默认行为

- 默认不启用 Ollama。
- 默认 backend 仍为 `rules`。
- 不影响现有评分系统运行。

## 二、启用 Ollama 的环境变量

```bash
EVOLUTION_LLM_BACKEND=ollama
OLLAMA_BASE_URL=http://localhost:11434
OLLAMA_MODEL=qwen3:0.6b
OLLAMA_TIMEOUT=120
```

## 三、推荐模型

- 连通性测试：`qwen3:0.6b`
- 日常评标增强：`deepseek-r1:32b`
- 通用备选：`qwen3:30b`
- 重大复核：`qwen3-next:80b-a3b-instruct-q8_0`

## 四、使用原则

- 测试时先用 `qwen3:0.6b`。
- Ollama 不可用时自动回退。
- 不要把 `.env` 提交到 Git。
- 不要让模型直接修改评分规则。
- 不要让模型写数据库。
- 不要直接替代人工评审。
- 不要默认启用 80B。

## 五、回退方式

- 去掉 `EVOLUTION_LLM_BACKEND=ollama`。
- 或将 backend 保持为 `rules`。
- Ollama 服务关闭时不应影响现有 `rules` 逻辑。

## 六、人工可控开关验证记录

已完成一次人工可控的 Ollama backend 开关验证，验证范围只覆盖学习进化增强后端，不接入核心评分主链。

- rules 默认模式：不设置 `EVOLUTION_LLM_BACKEND` 时，`backend=rules`，`ollama_called=false`。
- ollama 可选模式：设置 `EVOLUTION_LLM_BACKEND=ollama`、`OLLAMA_MODEL=qwen3:0.6b`、`OLLAMA_BASE_URL=http://localhost:11434`、`OLLAMA_TIMEOUT=120` 后，返回 `enhanced_by=ollama`，且 `content_non_empty=true`。
- 失败模型回退：设置 `OLLAMA_MODEL=not-exist-model` 时返回 `None`，不崩溃，可由 rules 逻辑安全兜底。

下一阶段仍应保持人工可控开关，不接核心评分主链；如需扩展到页面或评标增强入口，应单独小范围设计、验证和回滚。

## 七、手动 Ollama 预览 API 真实验证记录

已完成一次手动 Ollama evolution preview API 的本机真实验证，验证对象为：

```text
POST /api/v1/projects/{project_id}/evolve/ollama_preview
```

验证环境与结果：

- 当前验证 commit：`6776dbc feat: add manual Ollama evolution preview API`。
- Ollama 服务可访问。
- 验证模型 `qwen3:0.6b` 存在。
- 使用环境变量：
  - `EVOLUTION_LLM_BACKEND=ollama`
  - `OLLAMA_MODEL=qwen3:0.6b`
  - `OLLAMA_BASE_URL=http://localhost:11434`
  - `OLLAMA_TIMEOUT=120`
- 真实调用预览 API 成功：`status_code=200`。
- 成功场景返回：`enhanced_by=ollama`，`fallback=false`，返回内容非空。
- 失败模型 `not-exist-model` 返回：`fallback=true`，`error_summary="Ollama enhancement returned no result"`。
- 失败模型场景不崩溃。
- 未写入 `evolution_reports`。
- 未调用 `save_evolution_reports`。
- 未调用 `load_evolution_reports`。
- 未调用 `scorer` / `v2_scorer`。
- 未启动 Web 服务，未写 `.env`，未连接数据库。

该接口仅用于人工触发的 Ollama 增强预览，不写入正式 `evolution_reports`，不改变核心评分主链，也不修改正式评分分数、扣分逻辑或评分规则。

页面中的“自我学习与进化”区域可单独提供“手动 Ollama 预览”按钮，只调用该预览 API 并展示临时结果；该按钮仍不接核心评分主链，也不保存正式进化报告。

## 八、前端 Ollama 增强预览按钮真实验收记录

已完成一次前端“Ollama 增强预览”按钮的本机真实运行验收，验证对象为“自我学习与进化”区域中的人工预览按钮。

验收环境与结果：

- 当前验证 commit：`69817e4 feat: add Ollama preview UI button`。
- 按钮存在，位于“自我学习与进化”区域，按钮文本为“Ollama 增强预览”。
- 点击按钮后成功调用：

  ```text
  POST /api/v1/projects/{project_id}/evolve/ollama_preview
  ```

- 成功模型 `qwen3:0.6b` 返回：`status_code=200`，`enhanced_by=ollama`，`fallback=false`，返回内容非空。
- 失败模型 `not-exist-model` 返回：`status_code=200`，`fallback=true`，不崩溃。
- 未写入 `evolution_reports`。
- 未触发 `save_evolution_reports`。
- 未触发 `score_text` / `score_text_v2` / `compute_v2_rule_total`。
- 未修改评分分数。
- 未写 `.env`。
- 未连接数据库写入。

该按钮只用于人工触发 Ollama 增强预览，只展示临时预览结果，不写入正式 `evolution_reports`，不修改评分分数，不接核心评分主链。

下一阶段仍暂不接核心评分主链；如需继续扩展，应优先保持人工可控、小范围验证和可回退。
