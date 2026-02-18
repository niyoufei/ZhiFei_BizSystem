# 青天评标系统 V2（合肥）章节因子补丁落地说明

## 1. 版本与生效条件
- Requirement Pack 文件：`/config/qingtian_hefei_chapter_factors_v1.json`
- 版本：`qthf_chapter_factors_v1_2026-02-11`
- 生效条件：
  - `project.region == "合肥"`
  - `project.scoring_engine_version_locked` 以 `v2` 开头

## 2. 已落地的强制规则（Override）
- 全局风险-措施闭环（16维全覆盖）：
  - 每个维度都注入 mandatory requirement：`REQ-ALL-RISK-001-01 ... -16`
  - 要求同维度内出现“风险/难点词 + 措施词”，且证据单元相邻距离不超过 2（或同标题块）。
- 维度 01 强制标题与内容：`REQ-01-INFOMGMT-001`
  - 精确标题：`信息化管理`
  - 内容至少命中 2 类证据（模块类、操作类）。
- 维度 03 强制标题与内容：`REQ-03-GREEN-001`
  - 精确标题：`绿色工地`
  - 环保主题至少 3 类 + 措施词 + 验收词。
- 维度 02 劳保用品矩阵：`REQ-02-LAOBAO-001`
  - 必含：`劳保用品`、`配置`
  - 辅助项至少 2 条（数量/标准/发放/领用/台账等）。
- 维度 06 关键工序控制点表：`REQ-06-KEYPROC-001`
  - 必须识别表头：`工序内容｜重点难点｜措施｜验收`（或等价同窗口文本）。

## 3. 评分引擎联动（ScoringEngineV2）
- Coverage 已接入 requirement 命中率。
- 若某维 mandatory requirement 未命中：该维 `Coverage` 封顶 `1.0/2.5`。
- Closure 强化：
  - 必须同时具备 `analysis + solution` 才能拿到 Closure 满档。
- Lint 联动：
  - mandatory 未命中时，输出 `MissingRequirement`（含 pack 自定义 `why_it_matters` 与 `fix_template`）。

## 4. Requirement JSON Schema（实现口径）
```json
{
  "pack_id": "qingtian_hefei_chapter_factors_v1",
  "version": "qthf_chapter_factors_v1_2026-02-11",
  "enable_scope": {
    "region": "合肥",
    "scoring_engine_min": "v2"
  },
  "requirements": [
    {
      "id": "REQ-03-GREEN-001",
      "dimension_id": "03",
      "req_label": "维度03必须包含完整标题“绿色工地”及至少3类环保要点",
      "req_type": "presence",
      "mandatory": true,
      "weight": 2.0,
      "patterns": {
        "heading_exact": "绿色工地",
        "topic_terms_at_least3": {
          "topics": ["扬尘", "噪声", "污水", "固废", "节能", "节水", "节材"],
          "minimum": 3
        },
        "must_include_measure_and_acceptance": {
          "measure_terms": ["设置", "配置", "喷淋", "清运"],
          "acceptance_terms": ["检查", "验收", "巡检", "记录", "台账"]
        }
      },
      "lint": {
        "issue_code": "MissingRequirement",
        "severity": "high",
        "why_it_matters": "缺少“绿色工地”完整章节/内容，文明施工与环保控制缺乏可执行标准。",
        "fix_template": "【绿色工地】\\n对象：{扬尘/噪声/污水/固废/节能节水}\\n措施：{设施/启停条件/频次}由{责任岗位}实施\\n验收：{巡检/记录/整改闭环}，形成{台账/检查表}。"
      }
    }
  ]
}
```

## 5. API 请求/返回样例

### 5.1 重建项目 requirements（已接入 pack）
`POST /api/v1/projects/{project_id}/requirements/rebuild`

响应示例（节选）：
```json
[
  {
    "id": "REQ-01-INFOMGMT-001",
    "project_id": "p1",
    "dimension_id": "01",
    "req_label": "维度01必须包含完整标题“信息化管理”及闭环内容",
    "req_type": "presence",
    "mandatory": true,
    "weight": 2.0,
    "source_pack_id": "qingtian_hefei_chapter_factors_v1",
    "source_pack_version": "qthf_chapter_factors_v1_2026-02-11",
    "priority": 100.0,
    "version_locked": "qthf_chapter_factors_v1_2026-02-11"
  }
]
```

### 5.2 上传施组并评分（V2）
`POST /api/v1/projects/{project_id}/shigong`

响应报告关键字段（节选）：
```json
{
  "report": {
    "rule_total_score": 78.42,
    "rule_dim_scores": {
      "01": {
        "dim_score": 6.12,
        "subscores": {
          "Coverage": 1.0,
          "Closure": 2.0,
          "Landing": 1.62,
          "Specificity": 1.5
        }
      }
    },
    "lint_findings": [
      {
        "issue_code": "MissingRequirement",
        "dimension_id": "01",
        "severity": "high",
        "why_it_matters": "缺少“信息化管理”完整章节/内容，无法体现项目数字化管控闭环。",
        "fix_template": "【信息化管理】..."
      }
    ],
    "requirement_pack_versions": [
      "qthf_chapter_factors_v1_2026-02-11"
    ],
    "meta": {
      "engine_version": "v2",
      "region": "合肥",
      "scoring_engine_version": "v2",
      "requirement_pack_versions": [
        "qthf_chapter_factors_v1_2026-02-11"
      ]
    }
  }
}
```

## 6. 报告页面字段对齐（前后端）
- `report.rule_total_score` -> 施组总分（0-100）
- `report.rule_dim_scores[dim].subscores` -> Coverage/Closure/Landing/Specificity 子项
- `report.lint_findings[]` -> 编制纠偏问题卡片
- `report.suggestions[]` -> 优先优化动作（按 expected_gain 排序）
- `report.requirement_pack_versions[]` -> 当前命中的规则包版本快照（用于复盘）

## 7. 反演校准 6 对象（系统落地）
- `QT_RESULT`
  - 外部青天真实结果：`qt_total_score/qt_dim_scores/qt_reasons/raw_payload`
- `SCORE_SNAPSHOT`
  - 本次预测快照：`rule_total_score/rule_dim_scores/penalties/lint/suggestions`
- `DELTA_CASE`
  - 误差样本：`qt - pred` 差异与原因映射
- `FEATURE_ROW`
  - 校准训练特征行（维度分、扣分统计、文本统计特征）
- `PATCH_PACKAGE`
  - 误差驱动补丁候选（阈值/词库/规则参数）
- `DEPLOY_RECORD`
  - 补丁发布记录（shadow 指标、闸门判定、promote/rollback）

## 8. 自动化测试（本次新增）
- 文件：`tests/test_hefei_chapter_factors.py`
- 覆盖用例：
  1. 缺“信息化管理”标题 -> 触发 MissingRequirement + 01维 Coverage 封顶
  2. 缺“绿色工地”标题 -> 触发 MissingRequirement
  3. 仅写 `PPE` 不写“劳保用品” -> 触发 `REQ-02-LAOBAO-001` 未命中
  4. 06维缺少标准四列表头 -> 触发 `REQ-06-KEYPROC-001`
  5. 缺风险-措施闭环 -> 触发 `REQ-ALL-RISK-001-xx`
