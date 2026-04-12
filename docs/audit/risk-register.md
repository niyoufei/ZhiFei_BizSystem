# 风险登记册

## 1. 风险分级说明

- `P0`：已阻断当前基线、或会显著阻断安全重构
- `P1`：当前可运行，但后续改造极易触发回归
- `P2`：可延后，但会持续累积复杂度和误判成本

## 2. 风险清单

| ID | 等级 | 事实证据 | 风险说明 |
| --- | --- | --- | --- |
| R-001 | P0 | `make test` 失败：`.venv/bin/python -m pytest` 报 `pytest` namespace package；`make run` 失败：`.venv` 中 `fastapi` 为 namespace package 且无 `APIRouter` | 仓库默认命令面不稳定；会把环境故障误判成业务故障 |
| R-002 | P0 | `app/main.py` 约 `51985` 行，含 `172` 路由、内联 HTML/JS、后台 worker 与业务编排 | 主文件已超过安全改动阈值，任何局部改动都可能碰到非目标域 |
| R-003 | P0 | `feedback_*`、`qingtian_dual_track`、`*_views` 反向 `import app.main as main_mod`，而 `main.py` 又正向导入它们 | 这是运行时双向耦合，重构时极易引入隐式行为变化 |
| R-004 | P1 | `app/main.py:656-695` 持有材料解析 worker、锁、活动项目、缓存等模块级共享状态 | 并发、测试隔离和生命周期治理难度高 |
| R-005 | P1 | `app/main.py`、`app/cli.py`、`app/web_ui.py`、`app/app.py` 都存在直接文件读写 | 统一存储协议被旁路，审计与回滚边界不一致 |
| R-006 | P1 | `app/main.py:33836-33855` 直接写 `app/resources/lexicon.yaml` / `rubric.yaml` | 评分词库和规则配置更新未纳入统一仓储/快照协议 |
| R-007 | P1 | `/trial_preflight`、`/improvement_overview` 同时依赖 self-check、data hygiene、evaluation、ops_agents build 快照 | 运维巡检和学习闭环边界混叠，容易把“诊断展示层”改成“业务裁决层” |
| R-008 | P1 | 配置读取散落在 `main/storage/auth/runtime_security/rate_limit/llm_*` | 配置协议穿透，后续切换配置中心或引入 typed settings 成本高 |
| R-009 | P1 | 评分、证据、评分依据、对比诊断分布在 `engine.scorer`、`engine.v2_scorer`、`scoring_diagnostics`、`submission_diagnostics`、`engine.compare` | 这是解释性核心区，结构重构若没有回放基线会直接破坏“分数-证据-建议”一致性 |
| R-010 | P2 | `app/web_ui.py` 与 `app/app.py` 仍保留并行入口 | 长期会继续制造“哪条链路才是标准链路”的认知噪声 |
| R-011 | P2 | `app/resources/` 内存在多个 `.bak_*` 历史文件，`app/` 下也有多个 `main.py.bak_*` | 对人类维护者有误导风险，容易被误当作现行实现依据 |

## 3. 与可解释性直接相关的高风险区

这些地方一旦改错，不是“某个接口坏了”，而是系统核心定位会被破坏：

| 区域 | 为什么危险 |
| --- | --- |
| `app.engine.scorer` / `app.engine.v2_scorer` | 最终规则裁决和维度得分生成在这里 |
| `app.scoring_diagnostics` / `app.submission_diagnostics` | 证据追溯、评分依据、诊断解释在这里闭环 |
| `app.engine.compare` | 满分优化清单、直接替换文本、原位补充内容都从这里产出 |
| `app.main::_resolve_project_scoring_context` | 专家权重、evolution 权重、历史学习配置在这里汇合 |
| `app.main::_resolve_project_ground_truth_score_rule` / `_resolve_project_confirmed_score_scale_max` | 真实评标分制归一化在这里裁决 |
| `app.main::_refresh_project_reflection_objects` | ground truth -> delta case -> calibration sample 的桥接点 |

## 4. 当前基线下哪些模块最适合先拆

### 第一批适合拆

1. 材料接入与解析编排
2. 项目级评分应用服务
3. 学习/治理编排服务

原因：

- 它们在 `main.py` 中已经形成相对连续的函数簇
- 抽离后可以保持评分引擎、schema、storage 不动
- 对最终裁决规则的侵入最小

### 暂不适合先拆

1. `app.engine.scorer`
2. `app.engine.v2_scorer`
3. `app.scoring_diagnostics`
4. `app.submission_diagnostics`
5. `app.engine.compare`

原因：

- 这些是解释性和回放一致性的核心区
- 必须先有服务层边界和回放框架，再动内核
