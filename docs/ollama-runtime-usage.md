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
