"""Tests for report_formatter.py module."""

from __future__ import annotations

from app.engine.report_formatter import (
    HIGH_PRIORITY,
    _build_template_action,
    _collect_missing_tags,
    _format_four_parts,
    _improvement_actions,
    _qingtian_comment,
    _render_penalty_line,
    _safe_snippet,
    _tags_hint,
    _top_penalties,
    _truncate_cn,
    build_action_template_02,
    build_action_template_03,
    build_action_template_07,
    build_action_template_09,
    format_qingtian_word_report,
    format_summary,
)


# ============================================================================
# Tests for _safe_snippet
# ============================================================================
class TestSafeSnippet:
    """Tests for _safe_snippet function."""

    def test_with_valid_snippet(self):
        evidence = {"snippet": "这是一段证据文本"}
        assert _safe_snippet(evidence) == "这是一段证据文本"

    def test_with_none_evidence(self):
        assert _safe_snippet(None) == "未检索到证据片段"

    def test_with_empty_dict(self):
        assert _safe_snippet({}) == "未检索到证据片段"

    def test_with_empty_snippet(self):
        evidence = {"snippet": ""}
        assert _safe_snippet(evidence) == "未检索到证据片段"

    def test_with_none_snippet(self):
        evidence = {"snippet": None}
        assert _safe_snippet(evidence) == "未检索到证据片段"

    def test_with_other_fields(self):
        evidence = {"other": "value", "snippet": "正确片段"}
        assert _safe_snippet(evidence) == "正确片段"


# ============================================================================
# Tests for _truncate_cn
# ============================================================================
class TestTruncateCn:
    """Tests for _truncate_cn function."""

    def test_short_text_unchanged(self):
        text = "短文本"
        assert _truncate_cn(text, 60) == "短文本"

    def test_exact_length_unchanged(self):
        text = "a" * 60
        assert _truncate_cn(text, 60) == text

    def test_long_text_truncated(self):
        text = "a" * 100
        result = _truncate_cn(text, 60)
        assert len(result) == 61  # 60 chars + ellipsis
        assert result.endswith("…")

    def test_none_input(self):
        assert _truncate_cn(None, 60) == ""

    def test_whitespace_stripped(self):
        text = "  有空格的文本  "
        assert _truncate_cn(text, 60) == "有空格的文本"

    def test_custom_max_len(self):
        text = "这是一段较长的文本内容"
        result = _truncate_cn(text, 5)
        assert result == "这是一段较…"


# ============================================================================
# Tests for _qingtian_comment
# ============================================================================
class TestQingtianComment:
    """Tests for _qingtian_comment function."""

    def test_action_001_code(self):
        result = _qingtian_comment("P-ACTION-001", "任何消息")
        assert "措施缺" in result and "参数/频次/验收/责任" in result and "落地性不足" in result

    def test_empty_001_code(self):
        result = _qingtian_comment("P-EMPTY-001", "任何消息")
        assert result == "表述偏承诺型，缺可核查指标与闭环动作。"

    def test_other_code_with_message(self):
        result = _qingtian_comment("P-OTHER-001", "这是一条普通消息")
        assert result == "这是一条普通消息"[:40]

    def test_other_code_with_long_message(self):
        long_message = "a" * 100
        result = _qingtian_comment("P-OTHER-001", long_message)
        assert len(result) == 40

    def test_other_code_with_none_message(self):
        result = _qingtian_comment("P-OTHER-001", None)
        assert result == ""

    def test_other_code_with_empty_message(self):
        result = _qingtian_comment("P-OTHER-001", "")
        assert result == ""


# ============================================================================
# Tests for _render_penalty_line
# ============================================================================
class TestRenderPenaltyLine:
    """Tests for _render_penalty_line function."""

    def test_basic_penalty(self):
        penalty = {
            "code": "P-TEST-001",
            "deduct": 5,
            "message": "测试消息",
            "evidence_span": {"snippet": "证据片段"},
        }
        result = _render_penalty_line(penalty)
        assert "【P-TEST-001】" in result
        assert "扣5分" in result
        assert "原因：测试消息" in result
        assert "证据：证据片段" in result

    def test_with_evidence_field(self):
        penalty = {
            "code": "P-TEST-002",
            "deduct": 3,
            "message": "另一条消息",
            "evidence": {"snippet": "备选证据"},
        }
        result = _render_penalty_line(penalty)
        assert "证据：备选证据" in result

    def test_missing_fields(self):
        penalty = {}
        result = _render_penalty_line(penalty)
        assert "【】" in result
        assert "扣0分" in result

    def test_action_001_comment(self):
        penalty = {"code": "P-ACTION-001", "deduct": 2, "message": "x"}
        result = _render_penalty_line(penalty)
        assert "措施缺" in result and "参数/频次/验收/责任" in result


# ============================================================================
# Tests for _format_four_parts
# ============================================================================
class TestFormatFourParts:
    """Tests for _format_four_parts function."""

    def test_spark_called_true(self):
        dim_data = {
            "definition_points": ["要点1", "要点2"],
            "defects": ["缺陷1"],
            "improvements": ["改进1"],
            "evidence": [{"snippet": "证据1"}],
        }
        result = _format_four_parts("07", "危大工程", dim_data, True)
        assert "07 危大工程" in result
        assert "定义要点：要点1；要点2" in result
        assert "缺陷：缺陷1" in result
        assert "改进：改进1" in result
        assert "证据：证据1" in result

    def test_spark_called_false_with_hits(self):
        dim_data = {
            "hits": ["命中1", "命中2"],
            "evidence": [{"snippet": "证据"}],
        }
        result = _format_four_parts("09", "进度管理", dim_data, False)
        assert "09 进度管理" in result
        assert "命中1" in result
        assert "参数/频次/验收/责任等落地要素表述不足" in result

    def test_spark_called_false_no_hits(self):
        dim_data = {"hits": [], "evidence": []}
        result = _format_four_parts("02", "安全管理", dim_data, False)
        assert "未在文本中提取到明确要点" in result

    def test_empty_evidence_list(self):
        dim_data = {"evidence": []}
        result = _format_four_parts("03", "环保管理", dim_data, True)
        assert "证据：未检索到证据片段" in result


# ============================================================================
# Tests for _top_penalties
# ============================================================================
class TestTopPenalties:
    """Tests for _top_penalties function."""

    def test_sorted_by_deduct(self):
        report = {
            "penalties": [
                {"code": "A", "deduct": 3},
                {"code": "B", "deduct": 10},
                {"code": "C", "deduct": 5},
            ]
        }
        result = _top_penalties(report, limit=10)
        assert result[0]["code"] == "B"
        assert result[1]["code"] == "C"
        assert result[2]["code"] == "A"

    def test_respects_limit(self):
        report = {"penalties": [{"code": f"P{i}", "deduct": i} for i in range(20)]}
        result = _top_penalties(report, limit=5)
        assert len(result) == 5

    def test_empty_penalties(self):
        report = {"penalties": []}
        assert _top_penalties(report) == []

    def test_none_penalties(self):
        report = {"penalties": None}
        assert _top_penalties(report) == []

    def test_missing_penalties_key(self):
        report = {}
        assert _top_penalties(report) == []


# ============================================================================
# Tests for _collect_missing_tags
# ============================================================================
class TestCollectMissingTags:
    """Tests for _collect_missing_tags function."""

    def test_collects_action_001_tags(self):
        report = {
            "penalties": [
                {"code": "P-ACTION-001", "tags": ["missing_param", "missing_freq"]},
                {"code": "P-OTHER-001", "tags": ["ignored_tag"]},
            ]
        }
        result = _collect_missing_tags(report)
        assert "missing_param" in result
        assert "missing_freq" in result
        assert "ignored_tag" not in result

    def test_deduplicates_tags(self):
        report = {
            "penalties": [
                {"code": "P-ACTION-001", "tags": ["missing_param"]},
                {"code": "P-ACTION-001", "tags": ["missing_param", "missing_role"]},
            ]
        }
        result = _collect_missing_tags(report)
        assert result.count("missing_param") == 1

    def test_empty_penalties(self):
        report = {"penalties": []}
        assert _collect_missing_tags(report) == []

    def test_none_tags(self):
        report = {"penalties": [{"code": "P-ACTION-001", "tags": None}]}
        assert _collect_missing_tags(report) == []


# ============================================================================
# Tests for _tags_hint
# ============================================================================
class TestTagsHint:
    """Tests for _tags_hint function."""

    def test_with_known_tags(self):
        tags = ["missing_param", "missing_freq"]
        result = _tags_hint(tags)
        assert "参数" in result
        assert "频次" in result

    def test_empty_tags(self):
        assert _tags_hint([]) == ""

    def test_unknown_tags(self):
        tags = ["unknown_tag"]
        assert _tags_hint(tags) == ""

    def test_all_known_tags(self):
        tags = ["missing_param", "missing_freq", "missing_acceptance", "missing_role"]
        result = _tags_hint(tags)
        assert "参数" in result
        assert "频次" in result
        assert "验收" in result
        assert "责任" in result


# ============================================================================
# Tests for build_action_template functions
# ============================================================================
class TestBuildActionTemplates:
    """Tests for build_action_template_* functions."""

    def test_template_07(self):
        result = build_action_template_07([])
        assert "危大清单" in result
        assert "监测预警" in result

    def test_template_09(self):
        result = build_action_template_09([])
        assert "总控/月/周/日计划" in result
        assert "关键线路" in result

    def test_template_02(self):
        result = build_action_template_02([])
        assert "风险分级" in result
        assert "隐患排查" in result

    def test_template_03(self):
        result = build_action_template_03([])
        assert "围挡" in result
        assert "扬尘治理" in result


# ============================================================================
# Tests for _build_template_action
# ============================================================================
class TestBuildTemplateAction:
    """Tests for _build_template_action function."""

    def test_dim_07(self):
        result = _build_template_action("07", [])
        assert "危大清单" in result

    def test_dim_09(self):
        result = _build_template_action("09", [])
        assert "总控" in result

    def test_dim_02(self):
        result = _build_template_action("02", [])
        assert "风险分级" in result

    def test_dim_03(self):
        result = _build_template_action("03", [])
        assert "围挡" in result

    def test_other_dim(self):
        result = _build_template_action("99", [])
        assert "【责任岗位】" in result
        assert "【频次】" in result

    def test_with_tags_appends_hint(self):
        result = _build_template_action("07", ["missing_param"])
        assert "（需补齐：参数）" in result


# ============================================================================
# Tests for _improvement_actions
# ============================================================================
class TestImprovementActions:
    """Tests for _improvement_actions function."""

    def test_includes_high_priority_dims(self):
        report = {"penalties": []}
        result = _improvement_actions(report)
        dim_ids = [r[0] for r in result]
        for hp in HIGH_PRIORITY:
            assert hp in dim_ids

    def test_action_001_generates_action(self):
        report = {"penalties": [{"code": "P-ACTION-001"}]}
        result = _improvement_actions(report)
        actions_text = [r[1] for r in result]
        assert any("补齐【频次】" in a for a in actions_text)

    def test_empty_001_generates_action(self):
        report = {"penalties": [{"code": "P-EMPTY-001"}]}
        result = _improvement_actions(report)
        actions_text = [r[1] for r in result]
        assert any("承诺改为量化指标" in a for a in actions_text)

    def test_respects_limit(self):
        report = {"penalties": []}
        result = _improvement_actions(report, limit=3)
        assert len(result) == 3

    def test_sorted_by_gain(self):
        report = {"penalties": [{"code": "P-ACTION-001"}, {"code": "P-EMPTY-001"}]}
        result = _improvement_actions(report)
        gains = [r[2] for r in result]
        assert gains == sorted(gains, reverse=True)


# ============================================================================
# Tests for format_summary
# ============================================================================
class TestFormatSummary:
    """Tests for format_summary function."""

    def test_basic_report(self):
        report = {
            "total_score": 75,
            "judge_mode": "rule",
            "judge_source": "local",
            "spark_called": False,
            "dimension_scores": {},
            "penalties": [],
        }
        result = format_summary(report)
        assert "《青天视角评分报告》" in result
        assert "总分（0-100）：75" in result
        assert "judge_mode：rule" in result

    def test_score_from_overall(self):
        report = {
            "overall": {"total_score_0_100": 80},
            "judge_mode": "spark",
            "judge_source": "api",
            "spark_called": True,
            "dimension_scores": {},
            "penalties": [],
        }
        result = format_summary(report)
        assert "总分（0-100）：80" in result

    def test_contains_all_sections(self):
        report = {
            "total_score": 70,
            "judge_mode": "rule",
            "judge_source": "local",
            "spark_called": False,
            "dimension_scores": {},
            "penalties": [],
        }
        result = format_summary(report)
        assert "A. 评分结论" in result
        assert "B. 高优维度诊断" in result
        assert "C. 扣分清单" in result
        assert "D. 一次性提升清单" in result
        assert "E. 附：证据索引说明" in result

    def test_with_penalties_renders_penalty_lines(self):
        """Test format_summary with penalties to cover _render_penalty_line call."""
        report = {
            "total_score": 65,
            "judge_mode": "rule",
            "judge_source": "local",
            "spark_called": False,
            "dimension_scores": {},
            "penalties": [
                {
                    "code": "P-ACTION-001",
                    "deduct": 2.5,
                    "message": "措施表述缺少落实要素",
                    "evidence_span": {"snippet": "测试证据片段"},
                },
                {
                    "code": "P-EMPTY-001",
                    "deduct": 1.5,
                    "message": "存在空泛承诺",
                    "evidence_span": {"snippet": "另一个证据"},
                },
            ],
        }
        result = format_summary(report)
        assert "C. 扣分清单" in result
        assert "P-ACTION-001" in result
        assert "P-EMPTY-001" in result


# ============================================================================
# Tests for format_qingtian_word_report
# ============================================================================
class TestFormatQingtianWordReport:
    """Tests for format_qingtian_word_report function."""

    def test_basic_report(self):
        report = {
            "total_score": 85,
            "judge_mode": "spark",
            "judge_source": "api",
            "spark_called": True,
            "overall": {"confidence_0_1": 0.9},
            "dimension_scores": {},
            "penalties": [],
        }
        result = format_qingtian_word_report(report)
        assert "《青天评标官终版报告》" in result
        assert "总分（0-100）：85" in result
        assert "置信度（0-1）：0.9" in result

    def test_score_from_overall_fallback(self):
        report = {
            "overall": {"total_score_0_100": 90, "confidence_0_1": 0.95},
            "judge_mode": "spark",
            "judge_source": "api",
            "spark_called": True,
            "dimension_scores": {},
            "penalties": [],
        }
        result = format_qingtian_word_report(report)
        assert "总分（0-100）：90" in result

    def test_contains_all_sections(self):
        report = {
            "total_score": 70,
            "judge_mode": "rule",
            "judge_source": "local",
            "spark_called": False,
            "overall": {},
            "dimension_scores": {},
            "penalties": [],
        }
        result = format_qingtian_word_report(report)
        assert "A. 评分结论" in result
        assert "B. 高优维度诊断" in result
        assert "C. 扣分清单" in result
        assert "D. 一次性提升清单" in result
        assert "E. 附：证据索引说明" in result


# ============================================================================
# Tests for HIGH_PRIORITY constant
# ============================================================================
class TestHighPriority:
    """Tests for HIGH_PRIORITY constant."""

    def test_contains_expected_dims(self):
        assert "07" in HIGH_PRIORITY
        assert "09" in HIGH_PRIORITY
        assert "02" in HIGH_PRIORITY
        assert "03" in HIGH_PRIORITY

    def test_order(self):
        assert HIGH_PRIORITY == ["07", "09", "02", "03"]


# ============================================================================
# Tests for i18n integration
# ============================================================================
class TestI18nIntegration:
    """Tests for multi-language support in report formatter."""

    def test_format_summary_chinese_locale(self):
        """Test format_summary with Chinese locale."""
        report = {
            "total_score": 85,
            "judge_mode": "rule",
            "judge_source": "local",
            "spark_called": False,
            "dimension_scores": {},
            "penalties": [],
        }
        result = format_summary(report, locale="zh")
        assert "《青天视角评分报告》" in result
        assert "总分（0-100）" in result
        assert "高优维度权重策略" in result

    def test_format_summary_english_locale(self):
        """Test format_summary with English locale."""
        report = {
            "total_score": 85,
            "judge_mode": "rule",
            "judge_source": "local",
            "spark_called": False,
            "dimension_scores": {},
            "penalties": [],
        }
        result = format_summary(report, locale="en")
        assert "Qingtian Scoring Report" in result
        assert "Total Score (0-100)" in result
        assert "High-Priority Dimension Weight Strategy" in result

    def test_format_qingtian_word_report_english_locale(self):
        """Test format_qingtian_word_report with English locale."""
        report = {
            "total_score": 90,
            "judge_mode": "spark",
            "judge_source": "api",
            "spark_called": True,
            "overall": {"confidence_0_1": 0.95},
            "dimension_scores": {},
            "penalties": [],
        }
        result = format_qingtian_word_report(report, locale="en")
        assert "Qingtian Official Scoring Report" in result
        assert "Confidence (0-1)" in result
        assert "Penalty List" in result

    def test_render_penalty_line_english(self):
        """Test _render_penalty_line with English locale."""
        penalty = {
            "code": "P-ACTION-001",
            "deduct": 5,
            "message": "Missing parameters",
            "evidence_span": {"snippet": "test evidence"},
        }
        result = _render_penalty_line(penalty, locale="en")
        assert "Deduct" in result
        assert "points" in result
        assert "Reason" in result
        assert "Qingtian Comment" in result

    def test_qingtian_comment_english(self):
        """Test _qingtian_comment with English locale."""
        result_action = _qingtian_comment("P-ACTION-001", "", locale="en")
        assert "parameter" in result_action.lower()
        assert "frequency" in result_action.lower()

        result_empty = _qingtian_comment("P-EMPTY-001", "", locale="en")
        assert "commitment" in result_empty.lower()

    def test_safe_snippet_english_fallback(self):
        """Test _safe_snippet with English locale fallback."""
        result = _safe_snippet(None, locale="en")
        assert result == "No evidence snippet found"

    def test_tags_hint_english(self):
        """Test _tags_hint with English locale."""
        tags = ["missing_param", "missing_freq"]
        result = _tags_hint(tags, locale="en")
        assert "Needs" in result
        assert "parameters" in result
        assert "frequency" in result

    def test_format_four_parts_english(self):
        """Test _format_four_parts with English locale."""
        dim_data = {
            "hits": ["Point 1"],
            "evidence": [{"snippet": "evidence text"}],
        }
        result = _format_four_parts("07", "TestDim", dim_data, spark_called=False, locale="en")
        assert "Key Points" in result
        assert "Defects" in result
        assert "Improvements" in result
        assert "Evidence" in result

    def test_improvement_actions_english(self):
        """Test _improvement_actions with English locale."""
        report = {
            "penalties": [
                {"code": "P-ACTION-001", "tags": ["missing_param"]},
            ],
        }
        result = _improvement_actions(report, limit=8, locale="en")
        # Check at least one action contains English text
        action_texts = [action for _, action, _ in result]
        assert any("Led by" in text for text in action_texts)

    def test_build_templates_english(self):
        """Test build_action_template functions with English locale."""
        result_07 = build_action_template_07([], locale="en")
        assert "Led by" in result_07
        assert "Technical Manager" in result_07

        result_09 = build_action_template_09([], locale="en")
        assert "Construction Worker" in result_09

        result_02 = build_action_template_02([], locale="en")
        assert "Safety Officer" in result_02

        result_03 = build_action_template_03([], locale="en")
        assert "dust control" in result_03.lower()

    def test_locale_none_defaults_to_chinese(self):
        """Test that locale=None defaults to Chinese output."""
        report = {
            "total_score": 85,
            "judge_mode": "rule",
            "judge_source": "local",
            "spark_called": False,
            "dimension_scores": {},
            "penalties": [],
        }
        result = format_summary(report, locale=None)
        assert "《青天视角评分报告》" in result
