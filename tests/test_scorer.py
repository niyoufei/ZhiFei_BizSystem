"""Tests for scorer.py edge cases to improve coverage."""

import pytest

from app.engine.scorer import (
    _dimension_weighted_score,
    _empty_promises_penalties,
    _has_grounding_elements,
)


class TestDimensionWeightedScore:
    """Tests for _dimension_weighted_score function."""

    def test_max_score_zero_returns_zero(self):
        """Line 15: when max_score <= 0, should return 0.0."""
        result = _dimension_weighted_score(score=5.0, max_score=0, weight=1.0)
        assert result == 0.0

    def test_max_score_negative_returns_zero(self):
        """Line 15: when max_score is negative, should return 0.0."""
        result = _dimension_weighted_score(score=5.0, max_score=-1, weight=1.0)
        assert result == 0.0

    def test_normal_calculation(self):
        """Normal case: calculates weighted score correctly."""
        result = _dimension_weighted_score(score=8.0, max_score=10.0, weight=2.0)
        assert result == pytest.approx(1.6)


class TestHasGroundingElements:
    """Tests for _has_grounding_elements function."""

    def test_pattern_match_returns_true(self):
        """Line 43: when pattern matches, should return True."""
        # Test numeric patterns with units
        assert _has_grounding_elements("采用100m3混凝土")
        assert _has_grounding_elements("厚度≤200mm")
        assert _has_grounding_elements("每日检查2次")

    def test_keyword_match_returns_true(self):
        """Line 46: when keyword matches, should return True."""
        # Test keyword matches
        assert _has_grounding_elements("项目经理负责")
        assert _has_grounding_elements("技术负责人审批")
        assert _has_grounding_elements("质检员检查")
        assert _has_grounding_elements("需要报验")
        assert _has_grounding_elements("验收流程")

    def test_no_match_returns_false(self):
        """When no pattern or keyword matches, should return False."""
        assert not _has_grounding_elements("空泛的承诺内容")
        assert not _has_grounding_elements("我们将确保质量")


class TestEmptyPromisesPenalties:
    """Tests for _empty_promises_penalties function with max_deduct limits."""

    @pytest.fixture
    def rubric_low_max_deduct(self):
        """Rubric with very low max_deduct to trigger early return."""
        return {
            "penalties": {
                "empty_promises": {
                    "deduct": 0.5,
                    "max_deduct": 0.5,  # Very low to trigger on first penalty
                    "window": 40,
                }
            }
        }

    @pytest.fixture
    def lexicon_with_keywords(self):
        """Lexicon with multiple keywords."""
        return {"empty_promises": {"keywords": ["确保", "保证", "严格"]}}

    def test_skip_when_has_grounding_elements(self, rubric_low_max_deduct, lexicon_with_keywords):
        """Line 65: skip span when it has grounding elements."""
        # Text with keyword but also has grounding element (项目经理)
        text = "项目经理确保质量管理到位"
        rubric = {
            "penalties": {
                "empty_promises": {
                    "deduct": 0.5,
                    "max_deduct": 3.0,
                    "window": 40,
                }
            }
        }
        penalties = _empty_promises_penalties(text, rubric, lexicon_with_keywords)
        # Should have no penalties because "项目经理" is a grounding element
        assert len(penalties) == 0

    def test_max_deduct_limit_in_inner_loop(self):
        """Line 63: return when max_deduct reached inside inner loop."""
        # Text with many empty promises without grounding elements
        text = "我们确保质量到位。我们确保进度到位。我们确保安全到位。"
        rubric = {
            "penalties": {
                "empty_promises": {
                    "deduct": 0.5,
                    "max_deduct": 0.5,  # Very low
                    "window": 20,
                }
            }
        }
        lexicon = {"empty_promises": {"keywords": ["确保"]}}
        penalties = _empty_promises_penalties(text, rubric, lexicon)
        # Should stop after first penalty due to max_deduct
        total_deduct = sum(p.deduct for p in penalties)
        assert total_deduct <= 0.5

    def test_max_deduct_reached_before_next_span(self):
        """Line 63: return at start of inner loop when max_deduct already reached."""
        # Multiple occurrences of same keyword, deduct equals max_deduct exactly
        # After first penalty, total_deduct == max_deduct, second span triggers line 63
        text = "我们确保质量。我们确保进度。我们确保安全。"
        rubric = {
            "penalties": {
                "empty_promises": {
                    "deduct": 1.0,
                    "max_deduct": 1.0,  # Exactly equal to deduct
                    "window": 10,
                }
            }
        }
        lexicon = {"empty_promises": {"keywords": ["确保"]}}
        penalties = _empty_promises_penalties(text, rubric, lexicon)
        # Should have exactly 1 penalty (first span), second span triggers early return
        assert len(penalties) == 1
        assert penalties[0].deduct == 1.0

    def test_max_deduct_limit_in_outer_loop(self):
        """Line 76: return when max_deduct reached at end of keyword iteration."""
        # Text with multiple different keywords, each triggering penalty
        text = "我们确保质量。我们保证进度。我们严格管理。"
        rubric = {
            "penalties": {
                "empty_promises": {
                    "deduct": 0.4,
                    "max_deduct": 0.8,  # Allow 2 penalties
                    "window": 15,
                }
            }
        }
        lexicon = {"empty_promises": {"keywords": ["确保", "保证", "严格"]}}
        penalties = _empty_promises_penalties(text, rubric, lexicon)
        # Should stop after 2 penalties
        total_deduct = sum(p.deduct for p in penalties)
        assert total_deduct <= 0.8

    def test_empty_keywords_no_penalties(self):
        """When no keywords defined, should return empty list."""
        text = "任意文本内容"
        rubric = {"penalties": {"empty_promises": {"deduct": 0.5, "max_deduct": 3.0, "window": 40}}}
        lexicon = {"empty_promises": {"keywords": []}}
        penalties = _empty_promises_penalties(text, rubric, lexicon)
        assert len(penalties) == 0
