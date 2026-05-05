# 青天 main worktree 未跟踪项分类盘点归档 v0.1.32

## 1. 文档用途

本文用于归档 Step 173H 对 `/Users/youfeini/Desktop/ZhiFei_BizSystem` main worktree 未跟踪项的只读分类盘点结果，记录当前数量、目录规模、风险分级、禁止清理边界和后续治理顺序。

本文只做 docs-only 盘点归档：
- 不清理任何未跟踪项；
- 不移动任何文件或目录；
- 不删除任何文件或目录；
- 不修改应用代码、测试、工具脚本、配置文件、`data/`、`output/`、`tmp/`、`docs/final/` 或 `docs/next/`。

## 2. 当前 main 对齐状态

当前 main 已与 `origin/main` 对齐：

- `main`: `96e9f145af480c2e1c2640938af727691ca5f5ad`
- `origin/main`: `96e9f145af480c2e1c2640938af727691ca5f5ad`
- `ahead/behind`: `0 / 0`
- 稳定标签：`v0.1.32-qingtian-main-worktree-untracked-governance`

当前结论：
- 分支状态已收口；
- 未跟踪项仍大量存在；
- 后续治理对象是未跟踪项本身，不再混入分支快进、提交或标签动作。

## 3. Step 173H 盘点结果

Step 173H 只读盘点结果如下：

- `untracked_total`: `9726`
- `exact_tracked_path_conflicts`: `0`
- `prefix_tracked_path_conflicts`: `0`

这表示当前未跟踪项与 `origin/main` 已跟踪文件不存在明显 exact / prefix 路径冲突，但这不代表未跟踪项可以直接清理。

## 4. 重点目录规模

| 路径 | 文件数 | 目录数 | 字节数 | 初步风险 |
|---|---:|---:|---:|---|
| `.playwright-cli/` | 581 | 0 | 28311237 | 中风险 |
| `data/` | 9248 | 98 | 2723941314 | 高风险 |
| `docs/final/` | 6 | 0 | 50764 | 高风险 |
| `docs/next/` | 33 | 0 | 172164 | 高风险 |
| `output/` | 600 | 102 | 110188116 | 高风险 |
| `tmp/` | 6 | 1 | 537831 | 中风险 |
| `tests/` | 245 | 1 | 16288822 | 中风险 |
| `青天评标.app/` | 8 | 5 | 975818 | 高风险 |

## 5. 分类结果

### 5.1 `data/`

`data/` 属于高风险目录，绝对禁止直接清理。

风险理由：
- 可能包含业务数据；
- 可能包含样本数据；
- 可能包含运行态数据；
- 当前规模较大，`files=9248`、`dirs=98`、`bytes=2723941314`。

后续要求：
- 先只读盘点；
- 再识别敏感性；
- 如需处置，必须先做仓库外备份；
- 禁止把真实业务数据提交入仓。

### 5.2 `output/`

`output/` 属于高风险目录，可能含导出或运行产物。

风险理由：
- 可能包含报告导出结果；
- 可能包含 Playwright 下载或截图；
- 可能包含运行态验证证据；
- 当前规模为 `files=600`、`dirs=102`、`bytes=110188116`。

后续要求：
- 禁止直接删除；
- 先按子目录分组；
- 再判断是否为可复核证据、临时产物或可归档产物。

### 5.3 `docs/final/` 与 `docs/next/`

`docs/final/`、`docs/next/` 属于高风险目录，可能含阶段交付物、恢复文档或历史工作记录。

当前规模：
- `docs/final/`: `files=6`、`bytes=50764`
- `docs/next/`: `files=33`、`bytes=172164`

后续要求：
- 禁止直接删除；
- 禁止直接移动；
- 先列出文档标题、用途和是否仍有交接价值；
- 再决定是否归档到仓库外或纳入后续正式文档。

### 5.4 `青天评标.app/`

`青天评标.app/` 属于高风险应用包。

风险理由：
- 可能是本地可运行应用；
- 可能是历史构建产物；
- 可能仍被用户或演示流程使用；
- 当前规模为 `files=8`、`dirs=5`、`bytes=975818`。

后续要求：
- 禁止直接删除；
- 禁止未确认来源前移动；
- 如需治理，应单独形成应用包处置方案。

### 5.5 `.playwright-cli/`

`.playwright-cli/` 属于自动化工具缓存或产物，后续可单独评估。

当前规模：
- `files=581`
- `bytes=28311237`

后续要求：
- 先确认是否仍需复现历史浏览器操作；
- 先按时间和文件名分组；
- 如判定可清理，也必须单项或单目录授权。

### 5.6 `tmp/`

`tmp/` 是临时文件候选，但仍需单项盘点。

当前规模：
- `files=6`
- `dirs=1`
- `bytes=537831`

后续要求：
- 不因目录名为 tmp 就直接删除；
- 先列出每个文件用途；
- 再按单项授权处理。

### 5.7 带空格副本文件

带空格副本文件需要 hash / diff 比对。

Step 173H 观察到：
- `space_copy_count`: `713`
- 主要集中在 `.playwright-cli/* 2.yml`；
- 另有 `pyproject 2.toml`；
- 另有 `scripts/browser_button_smoke 2.py`；
- 另有若干 Git quoted 路径形式的 `tests/* 2.py` 候选。

后续要求：
- 禁止未经 hash / diff 删除带空格副本；
- 小型副本文件优先比对；
- 与正式文件完全一致时，也必须先形成备份或清理候选清单。

### 5.8 `tests/* 2.py`

`tests/* 2.py` 需按测试副本候选处理。

特别说明：
- Step 173H 中 `test_copy_count=0` 是由于 Git quoted 路径导致脚本未识别；
- 不能据此判断没有测试副本；
- `git status --short` 已显示 9 个 `tests/* 2.py` 测试副本候选；
- 后续应使用能处理 quoted 路径的脚本重新识别测试副本。

已观察到的测试副本候选包括：
- `tests/conftest 2.py`
- `tests/test_compare 2.py`
- `tests/test_feature_distillation 2.py`
- `tests/test_ground_truth_feature_confidence 2.py`
- `tests/test_history 2.py`
- `tests/test_observability 2.py`
- `tests/test_scoring_diagnostics 2.py`
- `tests/test_trial_preflight 2.py`
- `tests/test_v2_scorer 2.py`

后续要求：
- 先与正式测试文件做 hash / diff 比对；
- 若完全一致，再列入候选清理清单；
- 若不一致，需输出差异摘要；
- 禁止未经备份删除 `tests/* 2.py`。

### 5.9 `pyproject 2.toml` 与 `scripts/browser_button_smoke 2.py`

`pyproject 2.toml`、`scripts/browser_button_smoke 2.py` 均需与正式文件比对。

后续要求：
- 先计算 hash；
- 再与 `pyproject.toml`、`scripts/browser_button_smoke.py` 做 diff；
- 不一致时输出差异摘要；
- 禁止直接删除。

## 6. 禁止事项

当前阶段禁止：

- 禁止 `git clean`；
- 禁止 `rm -rf`；
- 禁止一次性批量清理；
- 禁止直接删除 `data/`；
- 禁止直接删除 `output/`；
- 禁止直接删除 `docs/final/`；
- 禁止直接删除 `docs/next/`；
- 禁止直接删除 `青天评标.app/`；
- 禁止未经备份删除 `tests/* 2.py`；
- 禁止未经 hash / diff 删除带空格副本；
- 禁止把真实业务数据提交入仓；
- 禁止把未跟踪项治理与业务开发混在同一轮。

## 7. 后续治理顺序

建议后续按以下批次推进：

1. 第一批：只读识别 quoted 路径与测试副本候选。
   - 使用能正确处理 Git quoted 路径的脚本；
   - 重新识别 `tests/* 2.py`；
   - 输出候选清单。
2. 第二批：对小型副本文件做 hash / diff 比对。
   - 优先 `pyproject 2.toml`；
   - 优先 `scripts/browser_button_smoke 2.py`；
   - 优先 `tests/* 2.py`。
3. 第三批：对完全重复且可替代文件做仓库外备份方案。
   - 备份目录必须在仓库外；
   - 记录 hash；
   - 备份后仍需单项授权才可删除。
4. 第四批：单文件、单指令、单次授权处理。
   - 一次只处理一个明确文件；
   - 每次处理前后均输出 `git status --short`。
5. 第五批：高风险目录另行专项治理。
   - `data/`；
   - `output/`；
   - `docs/final/`；
   - `docs/next/`；
   - `青天评标.app/`。

任何阶段不得直接清理高风险目录。

## 8. 当前建议

当前不建议清理任何文件。

下一步应先进行 quoted 路径识别专项，只读确认 `tests/* 2.py`、带空格副本文件和正式路径之间的对应关系，再决定是否进入 hash / diff 比对。
