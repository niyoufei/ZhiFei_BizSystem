# AGENTS.md — ZhiFei_BizSystem 项目专属规则

## 1. 项目定位
这是一个“施工组织设计评标评分系统”项目。

主要能力包括：
- CLI 评分：`python -m app.cli score`
- 批量处理：`python -m app.cli batch`
- API 服务：`python -m app.main`
- Web/UI 启动：`make run` / `make web`
- Windows 桌面入口：`zhifei-secure-desktop = app.windows_desktop:main`

本项目不仅是代码仓库，也是“操作脚本 + 运维脚本 + 使用文档”仓库。
修改时必须保护已有命令面、脚本面、输出面和文档面的一致性。

---

## 2. 技术栈与兼容性

### 技术栈
- 纯 Python 项目，无 Node / npm / package.json 依赖
- 后端：FastAPI, Uvicorn
- 命令行：Typer
- 文档处理：python-docx, pymupdf, pypdf, pytesseract, pillow, openpyxl
- 其他：streamlit, slowapi, prometheus-client, numpy

### Python 版本约束
- `pyproject.toml` 以 Python 3.10+ 为基础
- `ruff` 目标版本为 `py310`
- 即使当前机器安装的是 Python 3.13，也不要引入 3.13 专属语法或行为假设
- 默认保持 **Python 3.10 ~ 3.12 兼容**

### 依赖维护规则
本项目同时维护：
- `pyproject.toml`
- `requirements.txt`

凡是新增、删除、升级依赖时，默认要检查这两处是否需要同步更新。
不要只改一处，导致安装方式分裂。

---

## 3. 修改前必须先读

开始改动前，优先阅读以下文件：
- `README.md`
- `pyproject.toml`
- `requirements.txt`
- `Makefile`
- `app/main.py`
- `app/cli.py`
- 与当前任务直接相关的 `app/`、`tests/`、`scripts/`、`docs/` 文件

如果改动涉及：
- Windows 桌面/安装：同时阅读 `docs/Windows安装与使用*.md`
- 零代码操作流程：同时阅读 `docs/零代码操作手册*.md`
- API/批处理/评分链路：先核对 README 中已有命令示例

---

## 4. 代码变更边界

### 总原则
- 优先做最小闭环修改
- 优先局部修复，不做无关重构
- 不因为“看起来可以整理”就大规模改名、搬文件或重排结构

### 默认不得随意破坏的外部接口
除非用户明确要求，否则不要随意更改：
- `python -m app.main`
- `python -m app.cli ...`
- `make run`
- `make api`
- `make web`
- `make smoke`
- `make doctor`
- `make e2e-flow`
- `make mece-audit`
- `make analysis-bundle`
- `make analysis-bundle-all`
- `make acceptance`
- `make acceptance-fast`
- `make spec-coverage`

也不要无故改变：
- CLI 参数名
- API 路由
- JSON 输出结构
- DOCX 输出行为
- Make 目标名
- README 中已有示例命令

---

## 5. 项目目录处理规则

### 视为“业务源码/配置来源”的目录与文件
优先把以下内容视为有效源码或规则来源：
- `app/`
- `tests/`
- `scripts/`
- `docs/`
- `README.md`
- `pyproject.toml`
- `requirements.txt`
- `Makefile`
- `.env.example`

### 视为“本地环境/生成物/历史痕迹”的内容
默认不要把以下内容当作当前业务实现，不要主动修改、清理或提交：
- `.env`
- `.env.*`（除 `.env.example`）
- `.venv/`, `venv/`, `env/`, `ENV/`
- `.venv_backup_*`
- `.venv_broken_*`
- `build/`, `dist/`, `site/`, `.eggs/`, `pip-wheel-metadata/`
- `__pycache__/`, `.pytest_cache/`, `.ruff_cache/`, `.mypy_cache/`
- `htmlcov/`, `.coverage*`
- `data/materials/`
- `data/submissions/`
- `data/*.json`
- `data/*.lock`
- `data/*.json.lock`
- `report_*.json`
- `sample_report*.json`
- `qingtian_report_word.txt`
- `*.bak`
- `*.bak_*`
- `app/main.py.bak_*`

### 特别说明
`app/main.py.bak_*`、`.venv_backup_*`、`.venv_broken_*` 都属于历史/本地痕迹。
不要把它们当成主分支实现依据，不要在这些文件上继续叠加修改。

---

## 6. 密钥与敏感信息规则

- 不读取、不展示、不回显真实 `.env` 内容
- 不把真实 API Key 写入代码、测试、README、脚本或提交说明
- 需要示例时，只引用 `.env.example`
- 示例值一律使用占位符，例如：
  - `your-api-key`
  - `your-openai-key`
  - `your-gemini-key`

涉及变量时，只讨论变量名，不讨论真实值，例如：
- `API_KEYS`
- `OPENAI_API_KEY`
- `OPENAI_MODEL`
- `GEMINI_API_KEY`
- `GEMINI_MODEL`
- `EVOLUTION_LLM_BACKEND`
- `SPARK_MODEL`

---

## 7. 兼容性与历史别名规则

根据 `.env.example` 的现状，本项目存在 LLM/后端兼容层。
如果修改配置解析或后端选择逻辑，默认要保留已有兼容行为，不要静默删掉。

重点保护：
- `EVOLUTION_LLM_BACKEND=spark` 的历史兼容语义
- `SPARK_MODEL` 作为旧别名的兼容路径
- 现有 OpenAI / Gemini 相关变量命名

除非用户明确要求“移除兼容层/升级配置协议”，否则不要擅自清除旧别名支持。

---

## 8. 本项目的实现偏好

### 对 CLI / API / 批处理 的要求
本项目有三条主链路：
- CLI
- API
- Batch

改动时优先保证三条链路语义一致：
- 输入格式支持保持一致
- 评分结果字段含义保持一致
- 错误提示尽量一致
- 同一能力不要在 CLI 和 API 中做成两套不同规则

### 对文档处理链的要求
涉及 TXT / DOCX / PDF / OCR 时：
- 优先保证可回退
- 优先保留清晰的报错信息
- 不要泄露原始敏感内容
- 不要在未确认的情况下重写用户原文件
- 新生成物优先放到 `build/` 或既有输出目录

### 对 Windows 入口的要求
项目存在 Windows 桌面打包/入口能力。
如果改动：
- `app.windows_desktop`
- Windows 安装说明
- 打包逻辑

则必须避免破坏现有 CLI / API 启动路径。

---

## 9. 代码风格与质量规则

以 `pyproject.toml` 为准：

- `ruff` line length: `100`
- 规则集合：`E`, `F`, `W`, `I`
- 忽略：`E501`

默认要求：
- 新代码遵守现有风格
- 不引入与当前项目风格冲突的大量格式化噪音
- 能小改就小改
- 对已有 public 函数、CLI 参数、API 字段名保持谨慎

---

## 10. 改动后的最小验证

根据改动范围至少执行对应验证：

### 小范围代码改动
- `python3 -m ruff check app/ tests/ scripts/`
或
- `make lint`

### 一般逻辑改动
- `python3 -m pytest tests/ -v`
或
- `make test`

### API / 启动链路改动
- `make smoke`
- 或最小启动验证：`python3 -m app.main`

### 评分 / 批处理改动
至少补一个实际命令验证，例如：
- `python3 -m app.cli score --input sample_shigong.txt`
- `python3 -m app.cli batch -i sample_shigong.txt -o build/batch`

### 更完整回归（按需）
- `make coverage`
- `make doctor`
- `make e2e-flow`
- `make mece-audit`
- `make acceptance-fast`
- `make acceptance`
- `make spec-coverage`

---

## 11. 变更说明输出格式

向用户汇报改动时，优先明确：

1. 改了哪些文件
2. 影响的是哪条链路
   - CLI
   - API
   - Web/UI
   - Batch
   - Windows 打包
3. 跑了哪些验证
4. 是否触及 `.env`、生成物、历史备份文件
5. 是否需要同步 README / docs / Makefile / tests

---

## 12. 本项目下的默认工作方式

Codex 在本项目中应默认采用以下工作顺序：

1. 先读 `README.md` / `pyproject.toml` / `Makefile`
2. 锁定本次任务影响的链路（CLI / API / Batch / Web / Windows）
3. 只改与任务直接相关的源码
4. 不碰 `.env`、本地数据、生成物、备份文件
5. 运行最小必要验证
6. 用“改动文件 + 影响链路 + 验证结果”的方式汇报
