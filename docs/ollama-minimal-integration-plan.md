# Ollama 最小接入计划

本文档只描述后续接入方案，不实现 Ollama 接入，不修改业务代码，也不要求本地安装或启动 Ollama。

## 目标

- 在保留现有规则评分、Spark 评分和进化 LLM 后端行为的前提下，规划一个可回退的本地 Ollama 后端。
- 优先用于“学习进化报告增强”和“编制指导生成”等低风险文字增强链路。
- 保持 CLI、API、Web/UI 的既有输出结构稳定，避免影响当前评分闭环。
- 将真实接入拆成独立小 PR，便于逐步评审、测试和回滚。

## 非目标

- 本计划 PR 不连接 Ollama。
- 本计划 PR 不新增依赖、不修改 `.env.example`、不修改 `app/`、`tests/`、`config/` 或 `scripts/`。
- 不改变系统名称、目录名称、CLI 参数名、API 路由或评分 JSON 结构。
- 不把 Ollama 设为默认后端。
- 不移除现有 `rules`、`spark`、`openai`、`gemini` 后端兼容路径。

## 当前基线

- CLI 评分当前支持规则模式，以及基于 Spark 的 `spark` / `hybrid` 模式。
- 学习进化后端当前通过 `EVOLUTION_LLM_BACKEND` 在 `rules`、`spark`、`openai`、`gemini` 之间选择。
- 未配置外部模型密钥或调用失败时，现有逻辑会回退到规则结果。
- 项目已经有 smoke test、Python 版本矩阵测试和 lint 检查，后续实现必须保持这些检查稳定。

## 最小设计建议

### 1. 后端边界

后续实现时，建议先只把 Ollama 接入学习进化增强链路，不直接参与核心评分总分计算。

建议新增后端语义：

- `EVOLUTION_LLM_BACKEND=ollama`
- `OLLAMA_BASE_URL=http://localhost:11434`
- `OLLAMA_MODEL=<local-model-name>`

默认仍保持 `EVOLUTION_LLM_BACKEND=rules`，只有用户显式配置 `ollama` 时才尝试本地调用。

### 2. 调用适配层

后续代码 PR 可新增独立适配文件，复用现有进化 LLM 的 prompt 构造和 JSON 解析规则：

- 输入：规则版进化报告与真实评标数据摘要。
- 输出：与现有 LLM 增强路径一致的 `high_score_logic`、`writing_guidance` 等文本字段。
- 失败：返回 `None` 或等价失败状态，由调用方继续使用规则版报告。

### 3. 安全与可回退

- 不自动探测或启动本机 Ollama 服务。
- 不把用户原始资料发送到任何远程地址；Ollama 地址必须由用户显式配置。
- 请求超时、服务不可达、模型不存在、返回非 JSON 时，全部回退到规则版输出。
- 状态接口只暴露是否配置、后端名、模型名和可用性摘要，不暴露敏感配置。

### 4. 文档与配置

后续实现 PR 再同步更新：

- `.env.example`：增加 Ollama 相关占位配置。
- README：补充启用方式、回退行为和故障排查。
- 产品说明：说明本地模型增强的适用范围与限制。

本计划 PR 不做上述配置变更，避免把尚未实现的能力暴露给用户。

## 建议拆分 PR

1. **适配层 PR**：新增 Ollama 进化后端适配器和单元测试，不接入 CLI/API 默认路径。
2. **配置接入 PR**：将 `EVOLUTION_LLM_BACKEND=ollama` 接入现有后端选择逻辑，补充状态展示。
3. **文档 PR**：补充 `.env.example`、README、产品说明和故障排查。
4. **可选体验 PR**：在 Web/UI 中展示本地模型增强状态，但不改变现有评分入口。

## 验收标准

后续实现完成前，至少满足：

- 默认配置下所有行为与当前 `rules` 后端一致。
- 未安装或未启动 Ollama 时，系统不报错、不阻塞、不改变评分结果。
- Ollama 调用失败时，进化报告仍能生成规则版结果。
- `python3 -m ruff check app/ tests/ scripts/` 通过。
- 相关单元测试覆盖成功、失败、超时、非 JSON、未配置等路径。
- `bash scripts/smoke_test.sh` 通过。

## 风险与待确认

- 本地模型的 JSON 稳定性可能弱于云端 API，需要严格解析与回退。
- 不同模型的上下文长度、中文能力和生成格式差异较大，需要在文档中明确推荐模型和最低资源要求。
- 如果未来让 Ollama 参与核心评分，必须单独设计分数约束、证据片段校验和一致性测试。
- 是否只用于学习进化，还是扩展到 CLI `score` 模式，需要在实现前单独确认。
