"""Unit tests for app.engine.compare module."""

from app.engine.compare import build_compare_narrative


def test_compare_insufficient_submissions():
    """Test with fewer than 2 submissions returns summary indicating insufficient data."""
    result = build_compare_narrative([])
    assert result["summary"] == "施组数量不足，无法进行对比分析。"
    assert result["top_submission"] == {}
    assert result["bottom_submission"] == {}
    assert result["key_diffs"] == []

    result_single = build_compare_narrative([{"total_score": 80}])
    assert "施组数量不足" in result_single["summary"]


def test_compare_two_submissions():
    """Test with exactly 2 submissions produces correct ranking."""
    submissions = [
        {
            "id": "sub1",
            "filename": "施组A.txt",
            "total_score": 75.0,
            "report": {
                "dimension_scores": {
                    "01": {"score": 8.0},
                    "02": {"score": 7.0},
                }
            },
        },
        {
            "id": "sub2",
            "filename": "施组B.txt",
            "total_score": 85.0,
            "report": {
                "dimension_scores": {
                    "01": {"score": 9.0},
                    "02": {"score": 8.5},
                }
            },
        },
    ]
    result = build_compare_narrative(submissions)

    assert result["top_submission"]["filename"] == "施组B.txt"
    assert result["top_submission"]["total_score"] == 85.0
    assert result["bottom_submission"]["filename"] == "施组A.txt"
    assert result["bottom_submission"]["total_score"] == 75.0
    assert len(result["key_diffs"]) == 2
    assert "85.0分" in result["summary"]
    assert "75.0分" in result["summary"]


def test_compare_multiple_submissions_sorted_correctly():
    """Test with multiple submissions returns highest and lowest correctly."""
    submissions = [
        {
            "id": "s1",
            "filename": "A.txt",
            "total_score": 60.0,
            "report": {"dimension_scores": {"01": {"score": 5}}},
        },
        {
            "id": "s2",
            "filename": "B.txt",
            "total_score": 90.0,
            "report": {"dimension_scores": {"01": {"score": 9}}},
        },
        {
            "id": "s3",
            "filename": "C.txt",
            "total_score": 75.0,
            "report": {"dimension_scores": {"01": {"score": 7}}},
        },
    ]
    result = build_compare_narrative(submissions)

    assert result["top_submission"]["id"] == "s2"
    assert result["bottom_submission"]["id"] == "s1"


def test_compare_key_diffs_limited_to_5():
    """Test that key_diffs returns at most 5 items."""
    dims = {f"0{i}": {"score": float(i)} for i in range(1, 10)}
    dims_low = {f"0{i}": {"score": float(i) - 5} for i in range(1, 10)}
    submissions = [
        {
            "id": "high",
            "filename": "H.txt",
            "total_score": 95.0,
            "report": {"dimension_scores": dims},
        },
        {
            "id": "low",
            "filename": "L.txt",
            "total_score": 50.0,
            "report": {"dimension_scores": dims_low},
        },
    ]
    result = build_compare_narrative(submissions)

    assert len(result["key_diffs"]) <= 5
    # Diffs should be sorted by delta descending
    for i in range(len(result["key_diffs"]) - 1):
        assert result["key_diffs"][i]["delta"] >= result["key_diffs"][i + 1]["delta"]


def test_compare_detailed_report_contains_actionable_basis():
    """Detailed narrative should provide dimension/penalty diagnostics and priority actions."""
    submissions = [
        {
            "id": "s-low",
            "filename": "低分稿.txt",
            "total_score": 62.0,
            "report": {
                "dimension_scores": {
                    "07": {"score": 2.0, "evidence": []},
                    "09": {"score": 3.0, "evidence": []},
                },
                "penalties": [
                    {
                        "code": "P-ACTION-002",
                        "points": 1.2,
                        "reason": "措施缺少硬要素：role,accept",
                        "evidence_refs": [{"text_snippet": "仅写了加强管理，缺少责任人与验收"}],
                    }
                ],
            },
        },
        {
            "id": "s-high",
            "filename": "高分稿.txt",
            "total_score": 88.0,
            "report": {
                "dimension_scores": {
                    "07": {
                        "score": 8.0,
                        "evidence": [{"text_snippet": "明确了危大工程旁站与应急阈值"}],
                    },
                    "09": {
                        "score": 8.5,
                        "evidence": [{"text_snippet": "总控与周计划联动，偏差触发纠偏"}],
                    },
                },
                "penalties": [],
            },
        },
    ]
    result = build_compare_narrative(submissions)

    assert result["score_overview"]["score_gap"] > 0
    assert len(result["dimension_diagnostics"]) >= 1
    assert len(result["penalty_diagnostics"]) >= 1
    assert len(result["priority_actions"]) >= 1
    assert len(result["submission_scorecards"]) >= 1
    assert "rewrite_template" in result["dimension_diagnostics"][0]


def test_compare_submission_cards_with_page_hint():
    """Each submission card should include actionable rows with page hints when markers exist."""
    text_low = "[PAGE:1]\n工程概况与总述\n[PAGE:2]\n仅写了加强管理，缺少责任人与验收\n"
    pos = text_low.find("仅写了加强管理")
    submissions = [
        {
            "id": "s-low",
            "filename": "低分稿.pdf",
            "total_score": 60.0,
            "text": text_low,
            "report": {
                "dimension_scores": {"07": {"score": 2.0}, "09": {"score": 3.0}},
                "penalties": [
                    {
                        "code": "P-ACTION-002",
                        "points": 1.2,
                        "reason": "措施缺少硬要素：role,accept",
                        "evidence_refs": [
                            {
                                "locator": f"char:{pos}-{pos + 6}",
                                "text_snippet": "仅写了加强管理，缺少责任人与验收",
                            }
                        ],
                    }
                ],
            },
        },
        {
            "id": "s-high",
            "filename": "高分稿.pdf",
            "total_score": 88.0,
            "text": "[PAGE:1]\n危大工程专项方案与旁站验收\n",
            "report": {
                "dimension_scores": {"07": {"score": 8.0}, "09": {"score": 8.0}},
                "penalties": [],
            },
        },
    ]
    result = build_compare_narrative(submissions)

    cards = result.get("submission_optimization_cards") or []
    assert len(cards) >= 1
    low_card = next((c for c in cards if c.get("submission_id") == "s-low"), {})
    assert low_card.get("filename") == "低分稿.pdf"
    assert low_card.get("target_score") == 100.0
    assert (low_card.get("target_gap") or 0) > 0
    recs = low_card.get("recommendations") or []
    assert len(recs) >= 1
    assert any("页" in str(r.get("page_hint") or "") for r in recs)
    assert any("满分目标" in str(r.get("issue") or "") for r in recs)
    scorecards = result.get("submission_scorecards") or []
    low_scorecard = next((c for c in scorecards if c.get("submission_id") == "s-low"), {})
    assert low_scorecard.get("filename") == "低分稿.pdf"
    assert len(low_scorecard.get("loss_items") or []) >= 1
    assert len(low_scorecard.get("deduction_items") or []) >= 1


def test_compare_narrative_respects_five_scale_totals():
    submissions = [
        {
            "id": "sub1",
            "filename": "施组A.txt",
            "total_score": 4.1125,
            "report": {"dimension_scores": {"01": {"score": 8.0}}},
        },
        {
            "id": "sub2",
            "filename": "施组B.txt",
            "total_score": 4.6775,
            "report": {"dimension_scores": {"01": {"score": 9.0}}},
        },
    ]

    result = build_compare_narrative(submissions, score_scale_max=5)

    assert result["score_scale_max"] == 5
    assert result["score_scale_label"] == "5分制"
    assert "4.6775 / 5" in result["summary"]
    assert result["submission_optimization_cards"][0]["target_score"] == 5.0
    assert result["submission_scorecards"][0]["target_full_score"] == 5.0


def test_compare_narrative_carries_score_confidence_metadata():
    submissions = [
        {
            "id": "s-low",
            "filename": "低置信稿.pdf",
            "total_score": 76.0,
            "report": {
                "meta": {
                    "score_confidence_level": "low",
                    "score_self_awareness": {
                        "level": "low",
                        "score_0_100": 22.0,
                        "reasons": ["资料覆盖不足"],
                    },
                },
                "dimension_scores": {"07": {"score": 3.0}, "09": {"score": 4.0}},
                "penalties": [],
            },
        },
        {
            "id": "s-high",
            "filename": "高置信稿.pdf",
            "total_score": 88.0,
            "report": {
                "meta": {
                    "score_confidence_level": "high",
                    "score_self_awareness": {
                        "level": "high",
                        "score_0_100": 81.0,
                        "reasons": ["资料覆盖较完整"],
                    },
                },
                "dimension_scores": {"07": {"score": 8.0}, "09": {"score": 8.5}},
                "penalties": [],
            },
        },
    ]
    result = build_compare_narrative(submissions)
    assert result["top_submission"]["score_confidence_level"] == "high"
    assert result["bottom_submission"]["score_confidence_level"] == "low"
    assert result["score_overview"]["low_confidence_submission_count"] == 1
    scorecards = result.get("submission_scorecards") or []
    low_card = next((c for c in scorecards if c.get("submission_id") == "s-low"), {})
    assert low_card.get("score_confidence_level") == "low"
    assert low_card.get("score_confidence_score") == 22.0


def test_compare_narrative_uses_evidence_bonus_for_near_ties():
    submissions = [
        {
            "id": "s-raw-high",
            "filename": "原始分略高但证据弱.pdf",
            "total_score": 85.0,
            "report": {
                "meta": {
                    "score_confidence_level": "low",
                    "score_self_awareness": {
                        "level": "low",
                        "score_0_100": 24.0,
                        "structured_quality_avg": 0.12,
                        "structured_quality_type_rate": 0.0,
                        "retrieval_file_coverage_rate": 0.18,
                        "dimension_coverage_rate": 0.2,
                        "reasons": ["资料结构化质量偏弱"],
                    },
                },
                "dimension_scores": {"09": {"score": 7.0}},
                "penalties": [],
            },
        },
        {
            "id": "s-evidence-strong",
            "filename": "原始分略低但证据强.pdf",
            "total_score": 84.8,
            "report": {
                "meta": {
                    "score_confidence_level": "high",
                    "score_self_awareness": {
                        "level": "high",
                        "score_0_100": 89.0,
                        "structured_quality_avg": 0.88,
                        "structured_quality_type_rate": 1.0,
                        "retrieval_file_coverage_rate": 0.92,
                        "dimension_coverage_rate": 0.86,
                        "reasons": ["资料覆盖与结构化质量较强"],
                    },
                },
                "dimension_scores": {"09": {"score": 7.2}},
                "penalties": [],
            },
        },
    ]
    result = build_compare_narrative(submissions)
    assert result["top_submission"]["id"] == "s-evidence-strong"
    assert (
        result["top_submission"]["ranking_sort_score"]
        > result["bottom_submission"]["ranking_sort_score"]
    )
    assert (
        result["top_submission"]["ranking_evidence_bonus"]
        > result["bottom_submission"]["ranking_evidence_bonus"]
    )
    assert result["score_overview"]["ranking_mode"] == "total_score+evidence_bonus"
    assert result["score_overview"]["max_ranking_evidence_bonus"] > 0


def test_compare_submission_cards_use_full_score_gap_not_top_gap():
    """Dimension optimization should target full score, not only current project top."""
    submissions = [
        {
            "id": "s-low",
            "filename": "待优化稿.pdf",
            "total_score": 70.0,
            "report": {
                "dimension_scores": {"07": {"score": 7.0, "max_score": 10.0}},
                "penalties": [],
            },
        },
        {
            "id": "s-high",
            "filename": "项目最高稿.pdf",
            "total_score": 80.0,
            "report": {
                "dimension_scores": {"07": {"score": 8.0, "max_score": 10.0}},
                "penalties": [],
            },
        },
    ]
    result = build_compare_narrative(submissions)
    cards = result.get("submission_optimization_cards") or []
    low_card = next((c for c in cards if c.get("submission_id") == "s-low"), {})
    assert low_card.get("target_score") == 100.0
    dim_recs = [r for r in (low_card.get("recommendations") or []) if r.get("dimension") == "07"]
    assert len(dim_recs) >= 1
    rec = dim_recs[0]
    assert rec.get("target_delta_reduction") == 3.0
    assert rec.get("reference_top_score") == 8.0


def test_compare_fallback_evidence_snippet_when_dimension_evidence_missing():
    """When dimension evidence is empty, fallback snippet should be extracted from source text."""
    text_low = "[PAGE:1]\n本章说明专项施工工艺和工序流程。\n"
    submissions = [
        {
            "id": "s-low",
            "filename": "缺证据稿.pdf",
            "total_score": 65.0,
            "text": text_low,
            "report": {
                "dimension_scores": {"10": {"score": 1.0, "evidence": []}},
                "penalties": [],
            },
        },
        {
            "id": "s-high",
            "filename": "高分稿.pdf",
            "total_score": 85.0,
            "text": "[PAGE:1]\n专项施工工艺参数、样板先行和验收闭环。\n",
            "report": {
                "dimension_scores": {"10": {"score": 8.0, "evidence": []}},
                "penalties": [],
            },
        },
    ]
    result = build_compare_narrative(submissions)
    scorecards = result.get("submission_scorecards") or []
    low_scorecard = next((c for c in scorecards if c.get("submission_id") == "s-low"), {})
    loss_rows = low_scorecard.get("loss_items") or []
    dim10 = next((r for r in loss_rows if r.get("dimension") == "10"), {})
    assert dim10
    assert "未提取到证据片段" not in str(dim10.get("evidence") or "")
    assert "页" in str(dim10.get("page_hint") or "")
    assert "前文" in str(dim10.get("evidence_context") or "")
    assert "后文" in str(dim10.get("evidence_context") or "")


def test_compare_optimization_card_contains_executable_steps():
    """Direct execution checklist should include explicit steps and acceptance checklist."""
    submissions = [
        {
            "id": "s-low",
            "filename": "待改稿.pdf",
            "total_score": 60.0,
            "text": "[PAGE:1]\n仅写加强管理，后续落实。\n",
            "report": {
                "dimension_scores": {"09": {"score": 1.0}},
                "penalties": [
                    {"code": "P-ACTION-002", "points": 0.8, "reason": "措施缺少硬要素：role,accept"}
                ],
            },
        },
        {
            "id": "s-high",
            "filename": "高分稿.pdf",
            "total_score": 86.0,
            "text": "[PAGE:1]\n总控计划、周计划、节点销项、验收闭环。\n",
            "report": {"dimension_scores": {"09": {"score": 8.5}}, "penalties": []},
        },
    ]
    result = build_compare_narrative(submissions)
    cards = result.get("submission_optimization_cards") or []
    low_card = next((c for c in cards if c.get("submission_id") == "s-low"), {})
    recs = low_card.get("recommendations") or []
    assert recs
    assert any(str(r.get("chapter_hint") or "").strip() for r in recs)
    assert any("执行步骤" in str(r.get("rewrite_instruction") or "") for r in recs)
    assert any("核验清单" in str(r.get("acceptance_check") or "") for r in recs)
    assert any("改写前（摘录）" in str(r.get("before_after_example") or "") for r in recs)
    assert any("改写后（示例）" in str(r.get("before_after_example") or "") for r in recs)
    assert any("前文" in str(r.get("evidence_context") or "") for r in recs)
    assert any("执行清单" in str(r.get("execution_checklist") or "") for r in recs)
    assert any(str(r.get("priority_reason") or "").strip() for r in recs)


def test_compare_dimension_diagnostics_contains_weak_filenames():
    submissions = [
        {
            "id": "s-low",
            "filename": "低分稿A.pdf",
            "total_score": 65.0,
            "report": {"dimension_scores": {"10": {"score": 1.0}}},
        },
        {
            "id": "s-high",
            "filename": "高分稿A.pdf",
            "total_score": 90.0,
            "report": {"dimension_scores": {"10": {"score": 9.0}}},
        },
    ]
    result = build_compare_narrative(submissions)
    diagnostics = result.get("dimension_diagnostics") or []
    dim10 = next((d for d in diagnostics if d.get("dimension") == "10"), {})
    assert dim10
    assert isinstance(dim10.get("weak_filenames"), list)
    assert isinstance(dim10.get("weak_files_with_scores"), list)
    assert "低分稿A.pdf" in dim10.get("weak_filenames", [])
    assert any("低分稿A.pdf" in str(x) for x in dim10.get("weak_files_with_scores", []))
    assert dim10.get("top_filename") == "高分稿A.pdf"
    assert dim10.get("bottom_filename") == "低分稿A.pdf"


def test_compare_still_generates_synthetic_evidence_without_text():
    submissions = [
        {
            "id": "s-low",
            "filename": "缺文本稿.pdf",
            "total_score": 61.0,
            "text": "",
            "report": {
                "dimension_scores": {"11": {"score": 1.0}},
                "penalties": [
                    {"code": "P-EMPTY-002", "points": 0.8, "reason": "承诺缺少责任人和频次"}
                ],
            },
        },
        {
            "id": "s-high",
            "filename": "高分稿.pdf",
            "total_score": 88.0,
            "report": {"dimension_scores": {"11": {"score": 8.5}}, "penalties": []},
        },
    ]
    result = build_compare_narrative(submissions)
    cards = result.get("submission_optimization_cards") or []
    low_card = next((c for c in cards if c.get("submission_id") == "s-low"), {})
    recs = low_card.get("recommendations") or []
    assert recs
    assert all("未提取到证据片段" not in str(r.get("evidence") or "") for r in recs[:3])
