# 青天评标系统 · 按标自适应评分主干（增量 1–8 交付说明）

## 1. 本次交付定位

本次新增「按标（per-tender）自适应评分主干」，由 8 个增量组成。**全部为新增模块**，严格遵循既有纪律：

- 不修改核心评分主链：`app/engine/scorer.py`、`app/engine/v2_scorer.py`；
- 不修改 `app/storage.py`、`app/main.py`，不改变 `data/` 写入结构；
- 每个增量确定性、可独立回退；
- LLM 相关能力一律 `default-off`、`preview-only`、`affects_score=False`、不写库。

本文件只做交付说明与索引，不改变任何运行逻辑，不接核心评分主链。

## 2. 背景与依据（4 个真实标 + 10 份真实施组）

通过对合肥/铜陵 4 个真实标（运康骨科医院、铜陵市立医院、肥东幼儿园、包河道路）与其评标一览表、10 份真实中标/入围施组的核对，得到以下事实，构成本主干的设计依据：

- **真实评分是「单一 F 分 + 档位」**，口径随标剧变：分制（5 分 / 100 分）、分档阈值、考量项条目数（6 / 10）、评标办法（综合评估法 / 技术评分合理价格法）都不同 → 评分维度必须「按标加载」，不能用固定 16 维通吃；
- **聚合 = N 位评委（5 或 7，现场定）简单平均**，按「小数点后第三位四舍五入」保留两位（已用长春 4.44、庐金 82.10、凯扬 4.30 验证）；
- **关注度机理**：青天大模型按文本给「基准分」，评委可调「关注度」产生微扰；多数评委不调 → 分数几乎相同。故评委均分稳定可学；
- **四标实测无人进入优秀档**，全部落在良好档 → 现实目标是「本标良好档·全场最前」，不是绝对满分；
- **施组的「胜负含金量」随评标办法变**：综合评估法 + 价格踩满 → 施组定胜负（decisive）；价格有差 → 联动（coupled）；技术评分合理价格法 + 报价趋同 → 施组只是入围门槛、价格定胜负（gate）；
- **真实评审三轴**：针对性、可行性、语言精练度（招标文件明文），其中「针对性」是最大杠杆。

## 3. 模块清单（8 引擎 + 8 测试 + 4 配置）

| 增量 | 引擎模块 | 职责 | 测试 |
|---|---|---|---|
| 1 | `app/engine/tender_profile.py` | 按标加载评分口径（评标办法/分制/分档/考量项/硬红线/含金量）+ F 分→档位 + 跨标归一化 + 字段内百分位 | `tests/test_tender_profile.py` |
| 2 | `app/engine/target_mapping.py` | 内部规则分（0-100）→ 本标 F 分/档位/归一化/百分位/距下一档；预留 calibrator 钩子 | `tests/test_target_mapping.py` |
| 3 | `app/engine/strategy_advisor.py` | 施组含金量评估（decisive/coupled/gate）+ 动态目标（全场最前） | `tests/test_strategy_advisor.py` |
| 4 | `app/engine/tender_preflight.py` | 按标硬红线（篇幅/必含施工总平面图/多份判废/空泛即 0 分）；与既有 `preflight.py` 互补、不修改它 | `tests/test_tender_preflight.py` |
| 5 | `app/engine/judge_aggregation.py` | 评委均分（简单平均、四舍五入两位）+ 关注度建模（基准分/共识评委数/最离群评委） | `tests/test_judge_aggregation.py` |
| 6 | `app/engine/shigong_diagnostics.py` | 三轴诊断（针对性/可行性/语言精练度）+ 考量项覆盖 + 高分样板拆解 + ROI 优化清单 | `tests/test_shigong_diagnostics.py` |
| 7 | `app/engine/compilation_advisor.py` | 差异解释 + 针对性改写（`default-off`、`preview-only`、`affects_score=False`、LLM 可注入、确定性退化） | `tests/test_compilation_advisor.py` |
| 8 | `app/engine/text_calibration.py` | 诊断特征 → composite → 1D 校准（最小二乘）+ 留一法 MAE + 皮尔逊相关 + 部署闸门 | `tests/test_text_calibration.py` |

配置（4 个真实标）：`config/tender_profiles/`
- `yunkang_guke_2026BFFGZ50127.json`（综合评估法 / 5 分 / decisive）
- `tongling_shili_2026AFWGZ50330.json`（综合评估法 / 5 分 / coupled）
- `feidong_kindergarten_2026ADDGZ50033.json`（技术评分合理价格法 / 100 分 / gate）
- `baohe_roads_2025BFBGZ50935.json`（技术评分合理价格法 / 100 分 / gate / 7 评委）

## 4. 关键实证发现（增量 8）

用 10 份真实中标/入围施组做校准验证：

- **跨标混合**：composite 与真实分皮尔逊相关 r ≈ −0.12（≈无关），校准退化为预测均值，MAE「改进」是回归到均值的假象；
- **同一标内（包河道路 7 件，同 100 分制）**：r = +0.58、留一法 MAE 0.020 vs 线性基线 0.096 —— 诊断特征在同标内对真实分有真实（中等）预测力。

**结论：校准必须按标 / 按标类型做，不能跨标混合；同标内已具备可用信号。** 局限：样本仍偏少且多为高分件，需补低分/废标件以扩大方差。这也促使部署闸门从「只看 MAE」补充「看相关性」。

## 5. 自验

- 沙箱环境无法安装 pytest / 联网，故采用「标准库重放全部断言」自验：8 个测试文件约 127 条断言全部通过；
- 8 模块在真实标上端到端串通（配置→战略→目标→评委聚合→红线→诊断→映射→差异解释）；
- 合并前请在本机运行 `python3 -m pytest tests/ -q` 确认全量套件通过（本次为纯新增，预期不影响既有测试）。

## 6. 安全边界

- 未修改 `scorer.py` / `v2_scorer.py` / `storage.py` / `main.py`；
- 未改变 `data/` 写入结构；
- LLM 路径全部 `affects_score=False`、`preview-only`、`no-write`，后端默认 `rules`（不调用任何大模型）；
- 各模块独立、可单独回退。

## 7. 待接（均需单独授权 / 在用户机器进行）

1. **按标训练并部署校准器**：能力已具备（增量 8），缺更多同标样本，尤其低分 / 废标件以扩大方差；
2. **真实 LLM 后端接线**：复用既有 `llm_evolution`，用于把「针对性」从关键词密度升级为语义判断，以提升相关性；
3. **集成进 `main.py` 报告 / API**：会改动核心文件，按纪律需单独授权；
4. **统一离线分析器**：把 8 模块串成单条「施组 + 标号 → Markdown 报告」命令（可作为下一增量，仍不改 `main.py`）。

北极星指标：预测分与真实分的差距随【同标】样本增加而收敛。
