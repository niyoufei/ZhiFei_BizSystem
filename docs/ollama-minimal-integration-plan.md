# ZhiFei_BizSystem Ollama 接入最小改动清单

本文档用于记录后续低风险接入本地 Ollama 大模型的最小改动范围。当前任务只做接入计划，不修改业务代码、不新增依赖、不启动服务、不连接 Ollama、不连接数据库。

## 当前接入原则

- 先接入 `llm_evolution` 增强链路。
- 不直接改核心评分主流程。
- 默认仍使用现有 rules / spark / openai / gemini 逻辑。
- Ollama 后端默认关闭。
- 无 Ollama 服务时必须自动回退，不影响现有系统运行。
- 不改系统名称，不改目录名称。

## 后续建议新增或修改的文件

以下文件仅作为后续接入清单记录，本次不得实际修改这些代码文件。

| 文件 | 后续建议 |
|------|----------|
| `app/engine/llm_evolution.py` | 后续新增 `ollama` backend 分支。 |
| `app/engine/llm_evolution_ollama.py` | 后续新增 Ollama HTTP 调用模块。 |
| `app/schemas.py` | 后续补充 `LLMBackendStatus` 对 `ollama` 的描述。 |
| `.env.example` | 后续增加 `OLLAMA_BASE_URL`、`OLLAMA_MODEL`、`OLLAMA_TIMEOUT`。 |
| `tests/` | 后续增加无 Ollama 服务时自动回退 rules 的测试。 |
| `README.md` | 后续增加本地 Ollama 可选接入说明。 |

## 推荐配置

后续接入时建议先使用以下配置，并保持默认关闭：

```dotenv
OLLAMA_BASE_URL=http://localhost:11434
OLLAMA_MODEL=deepseek-r1:32b
OLLAMA_TIMEOUT=30
```

- 默认开关：关闭。
- 默认模型：`deepseek-r1:32b`。
- 备用模型：`qwen3:30b`。
- 复核模型：`qwen3-next:80b-a3b-instruct-q8_0`。

## 接入验证顺序

1. 只读检查配置。
2. 新增 Ollama 客户端模块。
3. 本地无 Ollama 时回退 rules。
4. 本地有 Ollama 时调用 `/api/chat`。
5. 只接 evolution 增强结果。
6. 确认稳定后再考虑评分主链是否接入。

## 风险边界

- 不允许模型直接修改评分规则库。
- 不允许模型直接写数据库。
- 不允许模型覆盖人工评分。
- 不允许将 `.env`、API key、密钥提交到 Git。
- 当前发现 `app/app.py` 存在示例弱口令风险，先记录为后续安全任务，不在本任务修复。

## 最小验收标准

- Ollama 默认关闭时，现有 CLI / API / Web / Batch 行为不变。
- 本地没有 Ollama 服务时，系统自动回退到 rules，不影响现有运行。
- 本地存在 Ollama 服务时，仅 `llm_evolution` 增强链路可选调用 Ollama。
- 接入前后不改变现有评分 schema、API 路由、CLI 参数和文档目录结构。
