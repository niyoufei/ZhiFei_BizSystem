# 青天评分报告与证据链交付说明

## 1. 文档用途

本文用于集中说明当前青天评标系统中的评分报告、证据链、评分依据、analysis bundle、DOCX、JSON、对比报告优化清单等交付物，帮助验收人员区分“可下载、可复制、可归档、可交接”的交付能力。

本文用于：
- 集中说明评分报告、证据链、评分依据、analysis bundle、DOCX、JSON、对比报告优化清单等交付物；
- 帮助验收人员区分“可下载、可复制、可归档、可交接”的交付能力；
- 明确哪些交付动作需要真实服务；
- 明确哪些动作不需要 Ollama；
- 明确哪些动作不接核心评分主链。

本文只做交付能力说明：
- 不改变运行逻辑；
- 不接核心评分主链；
- 不改变 data 写入结构；
- 不替代接口测试、CI 和 PR 记录；
- 文档检查、静态检查、mock 测试不需要启动服务，也不需要 `ollama serve`。

## 2. 交付物总表

| 交付物 | 主要用途 | 当前入口 | 交付形态 | 验收点 | 边界说明 |
|--------|----------|----------|----------|--------|----------|
| 评分报告 JSON | 接口验收、数据归档、自动化比对 | `score` / `rescore` / `reports/latest` 相关能力 | JSON / 结构化结果 | 分数、分项结果、问题或建议可读取 | 不在本文档任务中修改 `scorer.py` / `v2_scorer.py` |
| 评分报告 DOCX | 人工审阅、线下交接、归档 | CLI / Web UI / DOCX exporter 相关能力 | DOCX | 可下载、可归档、可交接 | 仅说明交付能力，不改变导出逻辑 |
| latest report | 核对最近一次评分结果 | `reports/latest` | 最近一次评分报告 | 可追溯最近一次提交或评分结果 | 真实访问 API 需要服务运行 |
| evidence trace | 追踪评分依据和证据路径 | `evidence_trace` | 证据追踪 / Markdown 或结构化结果 | 引用来源、评分依据或证据路径可核对 | 不修改 evidence 生成逻辑 |
| scoring basis | 说明评分依据、维度解释或评分理由 | `scoring_basis` | 评分依据 | 维度依据、解释信息或评分理由可核对 | 不接核心评分主链 |
| analysis bundle | 项目级归档与交接 | `analysis_bundle` | 分析包 / Markdown | 项目级交付资料可打包、可归档、可交接 | 真实生成需要服务运行或授权执行对应流程 |
| compare_report 对比报告 | 形成问题项、优化建议、推荐改写 | `compare_report` | 对比报告 / 优化建议 | 问题项、优化建议、推荐改写可读取 | 不重新评分、不触发 rescore |
| 优化清单复制文本 | 快速交底、粘贴到文档、人工复核 | 复制优化清单 | 纯文本 | 字段清楚、可粘贴、可交底 | 使用当前浏览器最近一次对比报告结果，不写 data |
| 优化清单 JSON | 归档、二次处理、AI 评审前后对照 | 导出优化清单 JSON | JSON | 可保存、可归档、可二次分析 | 不接 Ollama、不触发 rescore |
| Ollama 增强预览结果 | 人工增强预览和临时交付参考 | Ollama 增强预览 | 预览文本 / JSON | 可复制、可导出 | 仅预览，不写正式学习进化结果，不影响评分，不进入核心评分主链；真实调用前才需要 `ollama serve` |

## 3. 评分报告交付路径

- 评分报告 JSON 适合接口验收、数据归档、自动化比对。
- DOCX 报告适合人工审阅、线下交接、归档。
- latest report 适合核对最近一次评分结果。
- CLI、Web UI、API 的说明只做交付路径说明，不改变代码行为。
- 真实 API 或页面演示需要服务运行，需单独授权。
- 文档阅读和静态检查不需要服务。

## 4. 证据链与评分依据交付路径

- evidence units 是系统内支撑证据数据，不直接等同于面向用户的最终报告。
- evidence trace 用于追踪评分依据和证据路径。
- scoring basis 用于说明评分依据、维度解释或评分理由。
- analysis bundle 用于项目级归档与交接。
- 这些交付物用于可解释、可检查、可归档。
- 本文不修改 `evidence.py` / `evidence_units.py` / `storage.py`。
- 本文不改变 data 写入结构。

## 5. 对比报告与优化清单交付路径

- compare_report 用于形成问题项、优化建议、推荐改写。
- 复制优化清单适合快速交底、粘贴到文档、人工复核。
- 导出优化清单 JSON 适合归档、二次处理、AI 评审前后对照。
- `direct_apply_text` 只来自 `direct_apply_text || replacement_text || insertion_content`。
- 不回退到 `issue`、`insertion_guidance`、`rewrite_instruction`。
- 复制/导出只使用当前浏览器最近一次对比报告结果。
- 不重新评分。
- 不触发 rescore。
- 不写 data。
- 不接 Ollama。

## 6. 验收清单

| 验收项 | 通过标准 | 证据材料 | 不通过处理 |
|--------|----------|----------|------------|
| 评分报告 JSON 可交付 | JSON 或结构化结果可读取，包含分数、分项结果、问题或建议 | API 返回、CLI 输出、导出的 JSON | 先确认评分入口和样例数据，再定位接口或导出问题 |
| DOCX 报告可交付 | DOCX 文件可下载、可打开、可归档 | DOCX 文件或下载记录 | 先确认导出入口和 `python-docx` 依赖状态 |
| latest report 可定位 | 最近一次评分报告可通过 latest report 入口定位 | `reports/latest` 返回或页面展示 | 先确认提交已评分，再确认 submission_id |
| evidence trace 可解释 | 证据路径、引用来源或 Markdown 可核对 | evidence trace JSON / Markdown | 先确认项目和提交存在，并已具备评分结果 |
| scoring basis 可核对 | 维度依据、解释信息或评分理由可核对 | scoring basis 返回 | 先确认最新已评分施组和材料注入状态 |
| analysis bundle 可归档 | 项目级分析包可生成、可下载或可归档 | analysis bundle Markdown | 先确认项目存在且已授权运行态服务 |
| compare_report 可生成 | 问题项、优化建议、推荐改写可读取 | compare_report 返回或页面结果 | 先确认同一项目下有已评分施组 |
| 复制优化清单可粘贴 | 复制文本字段清楚，可交底、可粘贴 | 剪贴板文本或人工粘贴结果 | 先生成对比报告，再执行复制 |
| 导出优化清单 JSON 可保存 | JSON 可保存、可归档、可二次分析 | 导出的 JSON 文件或内容 | 先生成对比报告，再执行导出 |
| Ollama preview 边界清楚 | 明确仅预览，不写正式学习进化结果，不影响评分，不进入核心评分主链 | 页面说明、预览结果、边界文档 | 真实调用前确认 `ollama serve`，只读阶段不调用 |
| 不接核心评分主链 | 交付说明不把复制、导出、预览动作接入核心评分主链 | 文档说明和 PR 记录 | 如需接入评分主链，必须另行授权和设计 |
| 不改 `scorer.py` / `v2_scorer.py` / `storage.py` | 本专项不修改评分和存储核心文件 | git diff 文件列表 | 发现触碰即停止并拆分任务 |
| 不提交 `.env` / 密钥 | 不出现 `.env`、token、password、API key 明文提交 | git status、diff、PR 文件列表 | 立即停止提交，先移除敏感信息 |

## 7. 需要服务或授权的动作

以下动作不是默认只读动作，当前文档任务不执行这些动作，均需用户单独授权：

- 真实访问 API 需要服务运行；
- 真实页面演示需要服务运行；
- 真实评分 / rescore 会走运行态业务流程；
- 真实 analysis bundle 生成需要运行态服务或授权流程；
- 真实 Ollama 调用前才需要 `ollama serve`；
- `doctor.sh`、`restart_server.sh`、`data_hygiene repair`、`e2e_api_flow.sh`、`git clean`、reset、force push 都不是默认只读动作。

## 8. 与现有阶段成果关系

| 阶段标签 | 相关交付能力 | 说明 |
|----------|--------------|------|
| `v0.1.7-qingtian-copy-export` | compare_report 对比报告、优化清单复制、导出优化清单 JSON、Ollama preview 复制/导出 | 支撑对比报告与预览结果的可交付能力 |
| `v0.1.8-qingtian-health-stability` | 健康稳定运行命令边界、ops_agents runtime boundary | 支撑真实服务演示前的运行态授权判断 |
| `v0.1.9-qingtian-health-selfcheck-boundaries` | health / ready / self_check 边界 | 支撑服务健康、运行态诊断和只读口径区分 |
| `v0.1.10-qingtian-diagnostic-scripts-boundaries` | diagnostic scripts 副作用边界 | 支撑 doctor、restart、data_hygiene、e2e_api_flow 等动作的授权边界 |
| `v0.1.11-qingtian-stage-delivery-index` | 阶段标签、PR、验证摘要和回退定位 | 支撑交接、验收、复盘和回退定位 |
| `v0.1.12-qingtian-business-demo-acceptance` | 业务演示与试用验收路径 | 支撑创建项目、上传材料、评分施组、报告交付和安全边界说明 |

## 9. 后续建议

- 后续可补静态测试锁定报告 / 证据链交付文档关键词。
- 如需真实演示，另开授权轮启动服务。
- 新功能仍按“小任务、单分支、单 PR、CI 全绿后合并、阶段标签”推进。
