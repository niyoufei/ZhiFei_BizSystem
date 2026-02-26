from __future__ import annotations

from app.engine.v2_scorer import score_text_v2


def _minimal_lexicon() -> dict:
    return {
        "dimension_keywords": {},
        "empty_promises": {"keywords": []},
        "action_triggers": [],
        "definition": {"keywords": [], "regexes": []},
        "analysis": {"keywords": [], "regexes": []},
        "solution": {"keywords": [], "regexes": []},
    }


def test_anchor_missing_lint_generated() -> None:
    text = "本施组主要说明总体安排与一般措施。"
    anchors = [
        {"anchor_key": "key_milestones", "anchor_value": ["关键线路节点"]},
    ]
    report = score_text_v2(
        submission_id="s1",
        text=text,
        lexicon=_minimal_lexicon(),
        anchors=anchors,
        requirements=[],
    )
    codes = [str(x.get("issue_code") or "") for x in (report.get("lint_findings") or [])]
    assert "AnchorMissing" in codes


def test_anchor_mismatch_lint_generated_for_duration_conflict() -> None:
    text = "本项目工期 120 天，计划组织如下。"
    anchors = [
        {"anchor_key": "contract_duration_days", "value_num": 90, "anchor_value": "90天"},
    ]
    report = score_text_v2(
        submission_id="s2",
        text=text,
        lexicon=_minimal_lexicon(),
        anchors=anchors,
        requirements=[],
    )
    codes = [str(x.get("issue_code") or "") for x in (report.get("lint_findings") or [])]
    assert "ConsistencyConflict" in codes
    assert "AnchorMismatch" in codes


def test_v2_score_reaches_100_under_full_credit_conditions() -> None:
    evidence_units = []
    for dim_id in [f"{i:02d}" for i in range(1, 17)]:
        unit_text = f"REQ{dim_id} 由项目经理牵头，每日检查，报验签认闭环。"
        if dim_id == "07":
            unit_text += (
                " 危大工程重难点专项方案已论证；存在风险隐患并可能影响工期；"
                "控制在5%以内，每周复核；监测旁站并销项；设置应急预案与复工条件。"
            )
        if dim_id == "09":
            unit_text += (
                " 工期120天，总控计划、月计划、周计划、日计划齐全；"
                "关键线路、节点、里程碑倒排；劳动力机械材料保障冗余调配；"
                "偏差纠偏赶工调整；每日例会与周报。"
            )
        if dim_id == "15":
            unit_text += " 资源配置与工期120天完全一致。"
        evidence_units.append(
            {
                "dimension_primary": dim_id,
                "text": unit_text,
                "tag_definition": True,
                "tag_analysis": True,
                "tag_solution": True,
                "landing_param": True,
                "landing_freq": True,
                "landing_accept": True,
                "landing_role": True,
                "specificity_score": 1.0,
                "anchor_links": ["project_scope"],
            }
        )

    text = (
        " ".join([f"REQ{i:02d}" for i in range(1, 17)])
        + " 危大工程、重难点、专项方案、论证；存在风险隐患并可能影响工期；"
        + "控制在5%以内，每周2次复核，项目经理负责并执行报验验收；"
        + "设置监测旁站并销项；应急预案与复工条件明确。"
        + "总控计划、月计划、周计划、关键线路、里程碑；劳动力机械材料保障冗余调配；"
        + "发生偏差立即纠偏赶工调整；每日例会与周报。工期120天。"
    )
    requirements = [
        {
            "id": f"r{i:02d}",
            "dimension_id": f"{i:02d}",
            "req_type": "keyword",
            "mandatory": True,
            "req_label": f"REQ{i:02d}",
            "patterns": {"keywords": [f"REQ{i:02d}"]},
        }
        for i in range(1, 17)
    ]
    anchors = [
        {"anchor_key": "contract_duration_days", "value_num": 120},
        {"anchor_key": "quality_standard", "anchor_value": "合格"},
        {"anchor_key": "dangerous_works_list", "anchor_value": ["危大工程"]},
        {"anchor_key": "key_milestones", "anchor_value": ["里程碑"]},
    ]
    report = score_text_v2(
        submission_id="s-full",
        text=text,
        lexicon=_minimal_lexicon(),
        anchors=anchors,
        requirements=requirements,
        evidence_units=evidence_units,
    )
    assert report["dim_total_80"] == 80.0
    assert report["dim_total_90"] == 90.0
    assert report["consistency_bonus"] == 10.0
    assert report["rule_total_score"] == 100.0


def test_semantic_requirement_respects_minimum_hint_hits() -> None:
    report = score_text_v2(
        submission_id="s-semantic-min",
        text="本方案已明确 BIM 平台组织与校核流程。",
        lexicon=_minimal_lexicon(),
        anchors=[],
        requirements=[
            {
                "id": "r-sem",
                "dimension_id": "01",
                "req_type": "semantic",
                "mandatory": True,
                "req_label": "语义命中至少2项",
                "patterns": {"hints": ["BIM", "碰撞", "深化"], "minimum_hint_hits": 2},
            }
        ],
        evidence_units=[
            {
                "dimension_primary": "01",
                "text": "BIM 流程说明。",
                "tag_definition": True,
                "tag_analysis": True,
                "tag_solution": True,
                "landing_param": True,
                "landing_freq": True,
                "landing_accept": True,
                "landing_role": True,
                "specificity_score": 1.0,
                "anchor_links": [],
            }
        ],
    )
    req = (report.get("requirement_hits") or [{}])[0]
    assert req.get("hit") is False
    assert str(req.get("reason") or "").startswith("semantic_hints:")


def test_evidence_gate_caps_dim_score_when_evidence_and_mandatory_missing() -> None:
    report = score_text_v2(
        submission_id="s-evidence-cap",
        text="一般描述文本。",
        lexicon=_minimal_lexicon(),
        anchors=[],
        requirements=[
            {
                "id": "r-cap",
                "dimension_id": "01",
                "req_type": "presence",
                "mandatory": True,
                "req_label": "必须命中",
                "patterns": {"keywords": ["不存在关键词"]},
            }
        ],
        evidence_units=[
            {
                "dimension_primary": "01",
                "text": "由项目经理牵头，每日检查，报验签认闭环，阈值控制在5%以内。",
                "tag_definition": True,
                "tag_analysis": True,
                "tag_solution": True,
                "landing_param": True,
                "landing_freq": True,
                "landing_accept": True,
                "landing_role": True,
                "specificity_score": 1.0,
                "anchor_links": [],
            }
        ],
    )
    dim01 = (report.get("rule_dim_scores") or {}).get("01") or {}
    gate = dim01.get("evidence_gate") or {}
    assert gate.get("applied") is True
    assert float(dim01.get("dim_score", 0.0)) <= 6.2


def test_material_consistency_penalty_applies_when_cross_material_hits_low() -> None:
    requirements = [
        {
            "id": "mc-boq",
            "dimension_id": "13",
            "req_type": "material_consistency",
            "mandatory": True,
            "req_label": "BOQ一致性",
            "source_pack_id": "runtime_material_consistency",
            "patterns": {
                "must_hit_terms": ["工程量", "综合单价", "措施费"],
                "minimum_terms": 2,
                "material_type": "boq",
            },
        },
        {
            "id": "mc-drawing",
            "dimension_id": "14",
            "req_type": "material_consistency",
            "mandatory": True,
            "req_label": "图纸一致性",
            "source_pack_id": "runtime_material_consistency",
            "patterns": {
                "must_hit_terms": ["节点", "剖面", "深化"],
                "minimum_terms": 2,
                "material_type": "drawing",
            },
        },
    ]
    for idx in range(1, 7):
        requirements.append(
            {
                "id": f"rag-{idx}",
                "dimension_id": "01",
                "req_type": "semantic",
                "mandatory": False,
                "req_label": f"检索{idx}",
                "source_pack_id": "runtime_material_rag",
                "patterns": {"hints": [f"关键检索词{idx}"], "minimum_hint_hits": 1},
            }
        )

    report = score_text_v2(
        submission_id="s-material-consistency",
        text="本施组只描述一般组织架构和常规流程。",
        lexicon=_minimal_lexicon(),
        anchors=[],
        requirements=requirements,
    )
    penalty_codes = [str(p.get("code") or "") for p in (report.get("penalties") or [])]
    assert "P-MATCONS-001" in penalty_codes
    assert "P-MATCONS-002" in penalty_codes
    summary = report.get("material_consistency") or {}
    boq_stats = (summary.get("by_material_type") or {}).get("boq") or {}
    assert boq_stats.get("mandatory_hit") == 0
