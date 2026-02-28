# 施工组织设计评标评分系统 Makefile
# 提供一键操作命令

.PHONY: install test smoke score docx batch clean help coverage lint-fix pre-commit web run api restart stop status daemon-start daemon-stop daemon-status analysis-bundle analysis-bundle-all doctor e2e-flow mece-audit spec-coverage acceptance acceptance-fast

PYTHON ?= $(shell if [ -x .venv/bin/python ]; then echo .venv/bin/python; else echo python3; fi)

# 默认目标
help:
	@echo "施工组织设计评标评分系统"
	@echo ""
	@echo "直接使用（推荐）："
	@echo "  make install  - 首次使用：安装依赖"
	@echo "  make run      - 启动系统，浏览器打开 http://localhost:8000/ 即可使用"
	@echo "  make restart  - 后台重启服务（自动停止旧进程）"
	@echo "  make status   - 查看服务状态与健康检查"
	@echo "  make stop     - 停止后台服务"
	@echo "  make daemon-start - 用 launchd 常驻启动（macOS）"
	@echo "  make daemon-status - 查看 launchd 常驻状态（macOS）"
	@echo "  make daemon-stop - 停止 launchd 常驻服务（macOS）"
	@echo "  make doctor   - 自动诊断（必要时自动重启并执行后端自检）"
	@echo "  make acceptance - 严格验收（doctor + e2e + spec-coverage + pytest）"
	@echo "  make acceptance-fast - 快速严格验收（跳过pytest）"
	@echo "  make mece-audit - 生成项目级 MECE 诊断汇总（build/mece_audit_latest.*）"
	@echo "  make spec-coverage - 检查V2重构关键文件/API覆盖度"
	@echo "  make analysis-bundle PROJECT_ID=<id> - 导出项目分析包 Markdown"
	@echo "  make analysis-bundle-all - 导出全部项目分析包 Markdown"
	@echo "  make e2e-flow - 端到端全流程回归（生成 build/e2e_flow/summary.json）"
	@echo ""
	@echo "其他命令："
	@echo "  make test     - 运行单元测试"
	@echo "  make smoke    - 运行端到端 smoke test"
	@echo "  make score    - 运行评分（输出 JSON）"
	@echo "  make docx     - 运行评分（输出 DOCX）"
	@echo "  make batch    - 批量处理示例文件"
	@echo "  make all      - 运行完整端到端（JSON + DOCX）"
	@echo "  make clean    - 清理产物"
	@echo "  make lint     - 运行代码检查"
	@echo "  make lint-fix - 运行代码检查并自动修复"
	@echo "  make coverage - 查看测试覆盖率"
	@echo "  make web      - 启动 Streamlit Web UI"
	@echo "  make api      - 仅启动 API 服务（同 make run）"
	@echo "  make pre-commit - 运行 pre-commit hooks"
	@echo "  make pre-commit-install - 安装 pre-commit hooks 到 git"

# 安装依赖
install:
	$(PYTHON) -m pip install -r requirements.txt

# 运行单元测试
test:
	$(PYTHON) -m pytest tests/ -v

# 运行端到端 smoke test
smoke:
	./scripts/smoke_test.sh

# 运行评分（输出 JSON）
score:
	$(PYTHON) -m app.cli score --input sample_shigong.txt --out build/output.json

# 运行评分（输出 DOCX）
docx:
	$(PYTHON) -m app.cli score --input sample_shigong.txt --docx-out build/output.docx

# 批量处理示例文件
batch:
	$(PYTHON) -m app.cli batch -i sample_shigong.txt -i sample_shigong_action_missing.txt -i sample_shigong_empty_promises.txt -o build/batch_output --docx
	@echo ""
	@echo "产物路径："
	@echo "  - JSON: build/batch_output/*_report.json"
	@echo "  - DOCX: build/batch_output/*_report.docx"
	@echo "  - 汇总: build/batch_output/_batch_summary.json"

# 完整端到端（JSON + DOCX）
all:
	$(PYTHON) -m app.cli score --input sample_shigong.txt --out build/output.json --docx-out build/output.docx
	@echo ""
	@echo "产物路径："
	@echo "  - JSON: build/output.json"
	@echo "  - DOCX: build/output.docx"

# 清理产物
clean:
	rm -f build/*.json build/*.docx
	rm -rf __pycache__ app/__pycache__ app/engine/__pycache__ tests/__pycache__
	rm -rf .pytest_cache

# 代码检查
lint:
	$(PYTHON) -m ruff check app/ tests/ scripts/

# 测试覆盖率
coverage:
	$(PYTHON) -m pytest --cov=app --cov-report=term-missing

# 一键启动（推荐）：启动后浏览器打开 http://localhost:8000/ 即可使用
run:
	@echo "启动青天评标系统..."
	@echo "浏览器打开: http://localhost:8000/"
	@echo "按 Ctrl+C 停止"
	@$(PYTHON) -m app.main

# API 服务（同 run）
api:
	@$(PYTHON) -m app.main

# 后台重启服务（自动停旧进程）
restart:
	./scripts/restart_server.sh

# 停止后台服务
stop:
	./scripts/stop_server.sh

# 查看后台服务状态
status:
	./scripts/server_status.sh

# launchd 常驻启动（macOS）
daemon-start:
	./scripts/start_server_launchd.sh

# launchd 常驻停止（macOS）
daemon-stop:
	./scripts/stop_server_launchd.sh

# launchd 常驻状态（macOS）
daemon-status:
	./scripts/server_launchd_status.sh

# 自动诊断（必要时重启 + 调用后端自检）
doctor:
	./scripts/doctor.sh

# 导出项目分析包 Markdown
analysis-bundle:
	@if [ -n "$(PROJECT_ID)" ]; then \
		$(PYTHON) scripts/export_project_analysis_bundle.py --project-id "$(PROJECT_ID)"; \
	else \
		$(PYTHON) scripts/export_project_analysis_bundle.py; \
	fi

# 导出全部项目分析包 Markdown
analysis-bundle-all:
	$(PYTHON) scripts/export_all_analysis_bundles.py

# 端到端 API 全流程回归
e2e-flow:
	./scripts/e2e_api_flow.sh

# 项目级 MECE 审计汇总
mece-audit:
	./scripts/mece_audit.sh

# V2 规格覆盖度检查（关键文件 + API）
spec-coverage:
	$(PYTHON) scripts/check_v2_spec_coverage.py

# 严格验收（完整链路）
acceptance:
	./scripts/acceptance.sh

# 快速严格验收（跳过 pytest）
acceptance-fast:
	RUN_TESTS=0 ./scripts/acceptance.sh

# Web UI
web:
	streamlit run app/web_ui.py

# Pre-commit hooks
pre-commit:
	$(PYTHON) -m pre_commit run --all-files

# 安装 pre-commit hooks 到 git
pre-commit-install:
	$(PYTHON) -m pre_commit install

# Lint 自动修复
lint-fix:
	$(PYTHON) -m ruff check app/ tests/ scripts/ --fix
