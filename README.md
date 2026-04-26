# 施工组织设计（施组）评标评分系统 MVP

[![CI](https://github.com/youfeini/ZhiFei_BizSystem/actions/workflows/ci.yml/badge.svg)](https://github.com/youfeini/ZhiFei_BizSystem/actions/workflows/ci.yml)

本项目提供一个可解释、可配置的施组评标评分 MVP：基于规则引擎 + 词典/正则完成 16 维度评分与「三步闭环逻辑锁」评分，输出总分、证据片段与改进建议。

**本系统支持在 macOS 与 Windows 上安装并启动，同一套代码与数据格式，按下方对应系统操作即可。**

---

## 直接使用（三步即可）

**目标：你只负责启动，在浏览器里完成创建项目、上传施组、对比与洞察、自适应补丁等全部操作。**

| 步骤 | Windows | macOS / Linux |
|------|---------|----------------|
| 1. 安装依赖（仅首次） | 在项目目录打开 cmd，执行<br>`python -m pip install -r requirements.txt` | `make install` 或 `pip3 install -r requirements.txt` |
| 2. 启动系统 | 双击 **`scripts\start.bat`**<br>或 cmd 中执行 `python -m app.main` | `make run` 或 `bash scripts/start.sh` |
| 3. 使用 | 启动后浏览器会自动打开；未打开则访问 **http://localhost:8000/** | 终端会提示打开 http://localhost:8000/ |

3. **在浏览器中操作**
   - 创建项目 → 刷新项目列表并选择项目
   - 上传施组文件（TXT）→ 自动评分并保存
   - 使用「对比排名」「对比报告」「洞察」「生成学习画像」
   - 使用「自适应建议」「生成补丁」「验证效果」「应用补丁」（应用补丁需 API Key，可选）

无需再单独起 CLI 或配置脚本，**系统已搭好，启动即可用。**

---

### 公司内部 Windows 正式使用

在每台 Windows 电脑上安装、正式使用时，请按 **[Windows 安装与使用说明](docs/Windows安装与使用.md)** 操作，包含：一键启动、端口配置、常见问题、可选 API Key 与数据说明。

如需“只用单步命令”的最简操作方式，请看 **[零代码操作手册](docs/零代码操作手册.md)**。

如需评估本地 Ollama 大模型的低风险可选接入，请看 **[Ollama 接入最小改动清单](docs/ollama-minimal-integration-plan.md)**。

---

## 环境准备（可选阅读）

```bash
# 安装依赖
pip install -r requirements.txt
```

**依赖项**：fastapi, uvicorn, typer, pydantic, PyYAML, pytest, python-docx, pymupdf

## 快速开始（CLI / API 详情）

### 1. CLI 评分（推荐）

**支持输入格式**：`.txt`、`.docx` 和 `.pdf`

```bash
# 基础评分（输出 JSON 到终端）
python3 -m app.cli score --input sample_shigong.txt

# 评分 DOCX 输入文件
python3 -m app.cli score --input document.docx --out build/report.json

# 评分 PDF 输入文件
python3 -m app.cli score --input document.pdf --out build/report.json

# 保存 JSON 结果
python3 -m app.cli score --input sample_shigong.txt --out build/report.json

# 生成 DOCX 报告
python3 -m app.cli score --input sample_shigong.txt --docx-out build/report.docx

# 同时输出 JSON 和 DOCX
python3 -m app.cli score --input sample_shigong.txt --out build/report.json --docx-out build/report.docx
```

### 2. 批量处理（batch 子命令）

**支持输入格式**：`.txt`、`.docx` 和 `.pdf`

```bash
# 处理多个指定文件
python3 -m app.cli batch -i file1.txt -i file2.txt -o build/batch

# 处理目录下所有 txt 文件
python3 -m app.cli batch -i ./inputs/ -o build/batch --pattern "*.txt"

# 处理目录下所有 docx 文件
python3 -m app.cli batch -i ./inputs/ -o build/batch --pattern "*.docx"

# 处理目录下所有 pdf 文件
python3 -m app.cli batch -i ./inputs/ -o build/batch --pattern "*.pdf"

# 同时生成 DOCX
python3 -m app.cli batch -i sample_shigong.txt -i sample_shigong_action_missing.txt -o build/batch --docx

# 使用 4 个并行线程加速处理（推荐用于大批量文件）
python3 -m app.cli batch -i ./inputs/ -o build/batch --workers 4

# 并行处理 + DOCX 输出
python3 -m app.cli batch -i ./inputs/ -o build/batch --workers 4 --docx
```

批量处理会自动：
- 生成每个文件对应的 `{filename}_report.json`
- 可选生成 `{filename}_report.docx`（使用 `--docx`）
- 生成 `_batch_summary.json` 汇总报告
- 支持并行处理（使用 `--workers N` 指定线程数，N>1 时启用并行）

### 3. 缓存预热（warmup 子命令）

从文件加载文本并预热评分缓存，便于后续评分请求命中缓存：

```bash
# 从 .txt 文件预热（每行一条文本）
python3 -m app.cli warmup -i filelist.txt

# 并行预热（多线程）
python3 -m app.cli warmup -i filelist.txt -w 4

# 不跳过已缓存项、指定 TTL（秒）
python3 -m app.cli warmup -i filelist.txt --no-skip-existing --ttl 7200
```

支持输入：`.txt`（每行一条）、`.json`（数组）。详见 `python3 -m app.cli warmup --help`。

### 4. API 服务

```bash
python3 -m app.main
```

### 试车前综合体检（推荐）

在正式试车前，建议对当前项目执行一次综合体检，把系统自检、评分前置、MECE 诊断、学习进化状态和系统总封关状态收拢成一份报告：

```bash
make trial-preflight PROJECT_ID=<你的项目ID>
```

产物会写入：
- `build/trial_preflight_latest.json`
- `build/trial_preflight_latest.md`

说明：
- 该检查是只读的，不会改动项目数据。
- `trial_run_ready=true` 代表当前项目可进入试车。
- 即使报告含警告项，也会明确区分“阻断试车”与“仅影响系统长期收敛/总封关”的问题。

API 启动后访问 `POST http://localhost:8000/score`，请求示例：

```json
{
  "text": "施工组织设计文本......",
  "project_type": "装修工程"
}
```

#### API Key 认证（可选）

生产环境建议启用 API Key 认证保护写入操作端点：

```bash
# 设置 API Key（多个 key 用逗号分隔）
export API_KEYS="your-secret-key-1,your-secret-key-2"

# 启动 API
python3 -m app.main
```

**认证方式**（二选一）：

```bash
# 方式 1：Header 认证（推荐）
curl -X POST http://localhost:8000/score \
  -H "X-API-Key: your-secret-key-1" \
  -H "Content-Type: application/json" \
  -d '{"text": "施工组织设计文本..."}'

# 方式 2：Query 参数认证
curl -X POST "http://localhost:8000/score?api_key=your-secret-key-1" \
  -H "Content-Type: application/json" \
  -d '{"text": "施工组织设计文本..."}'
```

**查看认证状态**：

```bash
curl http://localhost:8000/auth/status
```

**环境变量示例**（见项目根目录 `.env.example`）：

```bash
# 可选：API 认证（多个 key 用逗号分隔）
API_KEYS=your-secret-key

# 可选：OpenAI 评分/进化凭证
OPENAI_API_KEY=your-openai-key
OPENAI_MODEL=gpt-5.4
```

**注意**：
- 如果未设置 `API_KEYS` 环境变量，则跳过认证（开发模式）
- GET 查询端点（如 `/projects`、`/auth/status`）保持公开
- POST 写入端点（如 `/score`、`/projects`）受认证保护

#### 请求限流（Rate Limiting）

系统内置了请求限流基础设施，可通过环境变量配置：

```bash
# 配置限流（可选）
export RATE_LIMIT_ENABLED=true      # 启用限流（默认 true）
export RATE_LIMIT_DEFAULT=100/minute # 默认限制
export RATE_LIMIT_SCORE=30/minute   # /score 端点限制
export RATE_LIMIT_UPLOAD=20/minute  # 上传端点限制
```

**查看限流状态**：

```bash
curl http://localhost:8000/rate_limit/status
```

### 5. OpenAI 评分模式（兼容 spark 别名）

CLI 当前实际使用 OpenAI GPT-5.4 进行评分或与规则混合评分。历史 `spark` 模式仍可继续输入，但只作为 `openai` 的兼容别名存在：

```bash
# 仅规则评分（默认）
python3 -m app.cli score --input sample_shigong.txt

# OpenAI 评分（推荐）
python3 -m app.cli score --input sample_shigong.txt --mode openai

# 历史 spark 别名（等价于 --mode openai）
python3 -m app.cli score --input sample_shigong.txt --mode spark

# 混合模式：规则分 + OpenAI 微调
python3 -m app.cli score --input sample_shigong.txt --mode hybrid
```

**配置**：设置 OpenAI 凭证：

```bash
export OPENAI_API_KEY=你的OpenAIKey
export OPENAI_MODEL=gpt-5.4
```

未设置时，`--mode openai` / `--mode spark` / `--mode hybrid` 会回退为规则结果，并在输出中注明 `fallback_reason`。旧 Spark 凭证不再作为真实评分凭证使用。

### 6. Web UI（推荐）

使用 Streamlit Web 界面进行交互式评分：

```bash
# 启动 Web UI
streamlit run app/web_ui.py

# 或使用 Makefile
make web
```

Web UI 功能：
- 支持上传 `.txt`、`.docx`、`.pdf` 文件
- 实时显示评分结果和各维度分数
- 显示扣分项和改进建议
- 一键下载 JSON 和 DOCX 报告

### 7. Web 首页（API 同机）

启动 API 后访问根路径可打开内置管理页：

```bash
python3 -m app.main
# 浏览器打开 http://localhost:8000/
```

若服务异常或端口被占用，推荐直接用：

```bash
make restart   # 后台重启（自动停旧进程）
make status    # 查看端口与健康检查
make stop      # 停止后台服务
make doctor    # 自动诊断（必要时自动重启 + 后端自检 + OpenAPI覆盖 + Web按钮契约）
make soak SOAK_DURATION=600 SOAK_INTERVAL=30  # 正式应用前长期稳定性巡航（默认10分钟）
# macOS 常驻模式（推荐长期开机运行）
make daemon-start
make daemon-status
make daemon-stop
# 严格诊断：缺少关键V2接口时返回失败
STRICT=1 make doctor
# 一键严格验收（doctor + e2e + browser smoke + mece + data hygiene + 覆盖检查 + 测试）
make acceptance

# 快速严格验收（同上，但跳过 pytest）
make acceptance-fast
```

功能包括：创建项目、刷新项目列表、上传资料/施组、对比排名、对比报告（叙述）、洞察、生成学习画像、自适应建议、生成补丁、验证效果、应用补丁（需 API Key）。应用补丁会同时更新 `lexicon.yaml` 与 `rubric.yaml` 并自动备份。

`make doctor` 现在会额外检查运行中服务的 OpenAPI 是否包含关键 V2 端点（如 `expert-profile`、`rescore`、`ground_truth/from_files`、`scoring/factors`、`system/self_check`），并校验首页关键按钮的 Web 契约与 smoke 覆盖门禁，便于快速识别“服务版本偏旧”或“按钮存在但未纳入自动验收”的问题。
`make soak` 会在正式应用前执行一轮长时稳定性巡航：先跑严格 `doctor`，再按周期采样监听进程、`/health`、`/api/v1/system/self_check` 与首页可达性，最后再次跑严格 `doctor`，并输出：
- `/Users/youfeini/Desktop/ZhiFei_BizSystem/build/stability_soak_latest.json`
- `/Users/youfeini/Desktop/ZhiFei_BizSystem/build/stability_soak_latest.md`
如果你希望服务持续在线（而不是按需拉起），可在 macOS 上使用 `make daemon-start`。
说明：如果项目位于 `Desktop` 路径且未授权给 launchd 访问，脚本会自动回退到普通后台重启模式；此时不是严格 keepalive，可用 `make daemon-status` 查看当前模式与原因。
`make daemon-status` 在 `mode=fallback` 且服务掉线时，会自动尝试重启一次（auto-heal），方便快速恢复。仅查看状态而不自动重启可用：`AUTO_HEAL=0 make daemon-status`。
如需强制尝试 launchd（不推荐），可执行：`FORCE_LAUNCHD=1 make daemon-start`。

若终端出现 `incompatible architecture`（如 `pydantic_core` 报错），说明虚拟环境依赖架构与当前终端不一致。请在项目目录重建：

```bash
rm -rf .venv
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
make restart
make status
```

#### 新增：16维关注度配置与一键重算（P0）

首页新增「青天评标关注度（16维）」区域：
- 16个滑杆（0..10）实时显示原始值与归一化权重（%）
- 可保存为专家配置并绑定项目
- 可一键“应用到本项目并重算所有施组”，重算后更新项目施组分数
- 每次评分会写入 `data/score_reports.json` 快照，保留历史可追溯
- 当项目 `status=submitted_to_qingtian` 时，配置保存/重算默认锁定，需二次确认并传 `force_unlock=true`

新增 API：

```bash
# 获取项目当前生效专家配置
GET /api/v1/projects/{project_id}/expert-profile

# 保存并绑定新的16维关注度配置
PUT /api/v1/projects/{project_id}/expert-profile
# body: { "name": "...", "weights_raw": { "01":5, ..., "16":5 }, "force_unlock": false }

# 使用当前配置重算项目施组
POST /api/v1/projects/{project_id}/rescore
# body: { "scoring_engine_version":"v2", "scope":"project", "force_unlock": false }
# 返回 409 表示项目已锁定，需显式 force_unlock=true 后重试
```

#### 新增：V2 反演校准链路（QT_RESULT -> SCORE_SNAPSHOT -> CALIBRATOR）

已新增最小闭环能力：
- `qingtian_results` 入库（真实青天结果）
- `score_reports` 最新快照查询（带 UI 摘要）
- `ridge` 校准器训练/部署/回填预测分

新增 API：

```bash
# 写入某条提交的青天真实结果
POST /api/v1/submissions/{submission_id}/qingtian-results
# body: { "qt_total_score": 86.5, "qt_dim_scores": {...}, "qt_reasons": [...], "raw_payload": {...} }

# 查询某条提交最新评分报告（含 ui_summary）
GET /api/v1/submissions/{submission_id}/reports/latest

# 查询某条提交最新青天结果
GET /api/v1/submissions/{submission_id}/qingtian-results/latest

# 训练校准器（当前仅 ridge）
POST /api/v1/calibration/train
# body: { "project_id": "...", "model_type": "ridge", "alpha": 1.0, "auto_deploy": true }

# 查询校准器版本
GET /api/v1/calibration/models

# 部署某个校准器版本（可选绑定项目）
POST /api/v1/calibration/deploy
# body: { "calibrator_version": "...", "project_id": "..." }

# 对项目历史报告回填 pred_total_score / pred_confidence
POST /api/v1/projects/{project_id}/calibration/predict
```

预评分列表增强：

```bash
# 返回包含 latest_report 的预评分主页结构（含 rank_by_pred/rank_by_rule）
GET /api/v1/projects/{project_id}/submissions?with=latest_report
```

新增反演进化对象 API：

```bash
# DELTA_CASE：重建/查询误差案例
POST /api/v1/projects/{project_id}/delta_cases/rebuild
GET  /api/v1/projects/{project_id}/delta_cases

# FEATURE_ROW：重建/查询校准样本
POST /api/v1/projects/{project_id}/calibration_samples/rebuild
GET  /api/v1/projects/{project_id}/calibration_samples

# PATCH_PACKAGE：挖掘/查询/影子评估/发布回滚
POST /api/v1/projects/{project_id}/patches/mine
GET  /api/v1/projects/{project_id}/patches
POST /api/v1/patches/{patch_id}/shadow_eval
POST /api/v1/patches/{patch_id}/deploy
# body: { "action": "deploy" } 或 { "action": "rollback", "rollback_to_version": "..." }

# 一键执行整条反演闭环（刷新案例/样本 + 训练部署校准器 + 回填预测分 + 补丁影子评估与发布）
POST /api/v1/projects/{project_id}/reflection/auto_run

# 项目级验收指标（V1 vs V2 vs V2+Calib）
GET /api/v1/projects/{project_id}/evaluation

# 跨项目汇总验收指标
GET /api/v1/evaluation/summary

# 批量录入真实评标（一次上传多个施组文件）
POST /api/v1/projects/{project_id}/ground_truth/from_files
# form-data:
# files=<可重复多个文件>, judge_scores="[80,81,82,83,84]", final_score=82, source="青天大模型"

# 评分体系总览（用于给外部模型分析当前评分因子）
GET /api/v1/scoring/factors
GET /api/v1/scoring/factors?project_id={project_id}

# 评分体系 Markdown 导出（可直接复制给 ChatGPT）
GET /api/v1/scoring/factors/markdown
GET /api/v1/scoring/factors/markdown?project_id={project_id}

# 项目分析包（评分体系 + 验收指标，一次性Markdown）
GET /api/v1/projects/{project_id}/analysis_bundle
GET /api/v1/projects/{project_id}/analysis_bundle.md

# 后端系统自检（结构化结果）
GET /api/v1/system/self_check
GET /api/v1/system/self_check?project_id={project_id}
```

说明：
- `scoring/factors` 返回的 `capability_flags` 可直接判断是否具备你关心的项：组织机构完善、章节内容完整、重难点、解决方案、图文要求。
- V2 lint 已包含：`MissingRequirement`、`AnchorMissing`、`AnchorMismatch`、`EmptyPromiseWithoutEvidence`、`ActionMissingHardElements`、`ClosureGap`、`ConsistencyConflict`。
- `adaptive` / `adaptive_patch` / `adaptive_apply` 响应内已包含 `source` 字段，可直接看“自适应优化”是否来自对比与洞察：当前基于历史评分报告与词库统计，不直接读取对比/洞察接口输出。

离线导出项目分析包（不依赖网页）：

```bash
# 指定项目ID导出
.venv/bin/python scripts/export_project_analysis_bundle.py --project-id <PROJECT_ID>

# 不指定时自动选择最近项目
.venv/bin/python scripts/export_project_analysis_bundle.py

# 或一键命令（可选指定项目）
make analysis-bundle PROJECT_ID=<PROJECT_ID>

# 一次导出全部项目分析包
make analysis-bundle-all

# 端到端回归（自动创建项目并完成主要流程）
make e2e-flow

# 严格模式：任一新接口缺失即失败（用于重构验收）
STRICT=1 make e2e-flow

# V2 规格覆盖度检查（关键文件 + API 覆盖）
make spec-coverage

# 一键严格验收（doctor + e2e + browser smoke + mece + data hygiene + spec-coverage + pytest）
make acceptance

# 正式应用前建议追加一次长时稳定性巡航（默认10分钟）
make soak
```

说明：
- 当你在 `ground_truth` 接口录入真实评标后，系统会自动同步到 `qingtian_results` 并刷新 `DELTA_CASE/FEATURE_ROW`，无需手工重复录入。
- 当补丁状态为 `deployed` 时，后续预评分会自动应用补丁中的 `penalty_multiplier`（影响 `rule_total_score`）。
- `make e2e-flow` 默认带兼容回退（会自动尝试 `/api/v1`、`/api` 及旧单文件端点）；若你要做新版本强约束验收，请加 `STRICT=1`。
- `make spec-coverage` 会产出 `build/v2_spec_coverage.json` 和 `build/v2_spec_coverage.md`，用于核对重构关键项是否齐全。
- `make acceptance` 会串行执行严格验收链路：`doctor(strict)` → `e2e-flow(strict)` → `browser_button_smoke` → `mece_audit(strict)` → `data_hygiene(auto-repair)` → `spec-coverage(strict)` → `pytest`。
- `make acceptance` 还会输出结构化验收摘要：`/Users/youfeini/Desktop/ZhiFei_BizSystem/build/acceptance_summary.json`（可直接发给 ChatGPT 做分析）。
- `make acceptance` 还会额外产出：
  - `/Users/youfeini/Desktop/ZhiFei_BizSystem/build/e2e_flow/acceptance_e2e.log`
  - `/Users/youfeini/Desktop/ZhiFei_BizSystem/build/browser_button_smoke.md`
  - `/Users/youfeini/Desktop/ZhiFei_BizSystem/build/web_button_contract.md`
- `make acceptance-fast` 与 `make acceptance` 相同，但跳过 `pytest`，适合高频快速自检。
- 当前关键按钮已全部进入 browser smoke，`web_button_contract.json` 中 `smoke_gap_count=0`，可直接用于判断“关键按钮是否还有漏网未进验收”。
- `make soak` 适合在准备进入正式应用前跑最后一轮巡航；若 `status=PASS`，代表起检、周期采样、收检三段都已通过。
- 验收摘要中的 `git` 字段会说明当前分支、是否已有提交、工作区是否有未提交变更，便于溯源。

golden_dataset 回放脚本：

```bash
# 生成 golden_dataset（优先选择样本数>=5的项目）
.venv/bin/python scripts/build_golden_dataset.py --min-samples 5

# 输出 V1/V2/当前分/V2+Calib 对比结果（JSON + Markdown）
.venv/bin/python scripts/evaluate_golden_dataset.py
```

离线迁移脚本（补齐历史项目字段、默认专家配置、报告快照）：

```bash
.venv/bin/python scripts/migrate_v2_p0.py
```

### 8. 自适应优化（应用补丁）

基于项目历史扣分统计，可生成并应用词库/规则补丁，使评分规则随项目数据微调：

| 操作 | 方法 | 说明 |
|------|------|------|
| 自适应建议 | `GET /api/v1/projects/{id}/adaptive` | 扣分统计与优化建议 |
| 生成补丁 | `GET /api/v1/projects/{id}/adaptive_patch` | 词库与 rubric 调整内容（预览） |
| 验证效果 | `GET /api/v1/projects/{id}/adaptive_validate` | 用当前配置重算历史提交，对比新旧分 |
| 应用补丁 | `POST /api/v1/projects/{id}/adaptive_apply` | 写回 lexicon.yaml + rubric.yaml（需 API Key，自动备份） |

应用补丁会追加空泛承诺/动作触发词到词库，并将提示写入 `rubric.yaml` 的 `adaptive_hints`，便于审计。

## 一键端到端运行

### 使用 Makefile（推荐）

```bash
make help      # 查看所有可用命令
make install   # 安装依赖
make all       # 完整端到端（JSON + DOCX）
make batch     # 批量处理示例文件
make test      # 运行单元测试
make smoke     # 运行端到端 smoke test
make lint      # 运行代码检查
```

### 使用 CLI

```bash
# 完整端到端流程：评分 + 生成 JSON + DOCX
python3 -m app.cli score --input sample_shigong.txt --out build/output.json --docx-out build/output.docx
```

**产物路径**：
- JSON 中间产物：`build/output.json`
- DOCX 报告：`build/output.docx`

## 验证命令

```bash
# 运行测试套件（405+ 测试，99% 覆盖率）
python3 -m pytest tests/ -v

# 运行代码风格检查
python3 -m ruff check .

# 运行端到端 smoke test
bash scripts/smoke_test.sh

# 查看测试覆盖率
python3 -m pytest --cov=app --cov-report=term-missing
```

## 代码质量（Pre-commit Hooks）

项目配置了 pre-commit hooks，在每次提交前自动检查代码质量。

```bash
# 安装 pre-commit hooks（仅需一次）
pip install pre-commit
python3 -m pre_commit install

# 手动运行所有检查
python3 -m pre_commit run --all-files

# 或使用 Makefile
make pre-commit-install  # 安装 hooks
make pre-commit          # 运行检查
make lint-fix            # 自动修复 lint 问题
```

**已配置的 hooks**：
- `trailing-whitespace` - 清理行尾空格
- `end-of-file-fixer` - 确保文件末尾换行
- `check-yaml/json` - 验证配置文件语法
- `check-merge-conflict` - 检查合并冲突标记
- `detect-private-key` - 检测私钥泄露
- `ruff` - Python 代码检查与格式化
- `pyproject-fmt` - 格式化 pyproject.toml

## 配置与权重

- 权重、阈值与维度说明：`app/resources/rubric.yaml`
- 词典与正则模板：`app/resources/lexicon.yaml`

调整权重即可影响总分：总分公式为
`sum(维度得分 * 权重) + logic_lock_bonus - penalties`
其中维度权重与逻辑锁最大加分均在 `rubric.yaml` 中配置。

## 输出字段

- `total_score` - 总分
- `dimension_scores` - 16 维度得分详情
- `logic_lock` - 三步闭环逻辑锁评分
- `penalties` - 扣分项
- `suggestions` - 改进建议
- `meta` - 元数据

## 示例

示例输入：`sample_shigong.txt`
示例输出：`sample_report.json`

## 目录结构

```
/app
  main.py              # API 入口
  cli.py               # CLI 入口
  schemas.py           # 数据模型
  config.py            # 配置加载
  /engine
    scorer.py          # 评分引擎
    dimensions.py      # 维度评分
    logic_lock.py      # 三步闭环逻辑锁
    evidence.py        # 证据提取
    docx_exporter.py   # DOCX 导出
    report_formatter.py # 报告格式化
  /resources
    rubric.yaml        # 评分规则
    lexicon.yaml       # 词典配置
/tests                  # 405+ 测试用例，99% 覆盖率
  test_adaptive.py
  test_app_web.py
  test_cli.py
  test_compare.py
  test_config.py
  test_dimensions_smoke.py
  test_docx_exporter.py
  test_evidence.py
  test_insights.py
  test_learning.py
  test_llm_judge_spark.py
  test_logic_lock.py
  test_main.py
  test_penalties.py
  test_report_formatter.py
  test_schemas.py
  test_storage.py
  test_subscores.py
/scripts
  run_cli_demo.sh
/build                 # 产物输出目录
sample_shigong.txt
sample_report.json
requirements.txt
README.md
```

## 排错方式

1. **依赖问题**：确保运行 `pip install -r requirements.txt`
2. **Python 版本**：建议使用 Python 3.10+
3. **文件编码**：输入文件应为 UTF-8 编码
4. **输入格式**：支持 `.txt`、`.docx` 和 `.pdf` 格式，其他格式会报错
5. **查看详细日志**：检查 `build/clawdbot/audit.log`

## 下一步扩展为 LLM/RAG（接口预留说明）

本 MVP 的评分入口集中在 `app/engine/scorer.py` 的 `score_text()`。
后续可新增 `llm_scorer.py` 或 `rag_scorer.py`，保持与 `score_text()` 相同的输出结构：

1. **输入保持一致**：传入 `text` 与配置字典
2. **输出结构不变**：复用 `ScoreReport` schema
3. **可并行融合**：在 `score_text()` 中加入开关，根据配置选择 deterministic/LLM/RAG
4. **证据片段要求**：从 LLM/RAG 结果中抽取原文 span，仍回填 `EvidenceSpan`

这样可在不影响 API/CLI 的前提下平滑替换评分引擎。
