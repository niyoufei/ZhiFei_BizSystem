"""Unit tests for app/engine/learning.py module."""

from __future__ import annotations

from app.engine.learning import build_learning_profile


class TestBuildLearningProfileEmpty:
    """Tests for empty or missing inputs."""

    def test_empty_submissions_returns_empty_profile(self):
        """Empty list returns empty multipliers and rationale."""
        result = build_learning_profile([])
        assert result == {"dimension_multipliers": {}, "rationale": {}}

    def test_empty_report_in_submission(self):
        """Submission with empty report is handled gracefully."""
        submissions = [{"report": {}}]
        result = build_learning_profile(submissions)
        assert result == {"dimension_multipliers": {}, "rationale": {}}

    def test_missing_report_key(self):
        """Submission without report key is handled gracefully."""
        submissions = [{"other_key": "value"}]
        result = build_learning_profile(submissions)
        assert result == {"dimension_multipliers": {}, "rationale": {}}

    def test_missing_dimension_scores(self):
        """Report without dimension_scores is handled gracefully."""
        submissions = [{"report": {"total_score": 80}}]
        result = build_learning_profile(submissions)
        assert result == {"dimension_multipliers": {}, "rationale": {}}


class TestBuildLearningProfileLowScore:
    """Tests for low score dimensions (avg < 4.0)."""

    def test_single_low_score_gets_boost(self):
        """Single submission with low score gets 1.2 multiplier."""
        submissions = [{"report": {"dimension_scores": {"dim1": {"score": 3.0}}}}]
        result = build_learning_profile(submissions)
        assert result["dimension_multipliers"]["dim1"] == 1.2
        assert "关注度" in result["rationale"]["dim1"]

    def test_average_low_score_gets_boost(self):
        """Multiple submissions averaging below 4.0 get 1.2 multiplier."""
        submissions = [
            {"report": {"dimension_scores": {"dim1": {"score": 2.0}}}},
            {"report": {"dimension_scores": {"dim1": {"score": 4.0}}}},
        ]
        # Average: (2.0 + 4.0) / 2 = 3.0 < 4.0
        result = build_learning_profile(submissions)
        assert result["dimension_multipliers"]["dim1"] == 1.2

    def test_zero_score(self):
        """Zero score is handled correctly."""
        submissions = [{"report": {"dimension_scores": {"dim1": {"score": 0}}}}]
        result = build_learning_profile(submissions)
        assert result["dimension_multipliers"]["dim1"] == 1.2

    def test_score_just_below_four(self):
        """Score of 3.99 (just below 4.0) gets boost."""
        submissions = [{"report": {"dimension_scores": {"dim1": {"score": 3.99}}}}]
        result = build_learning_profile(submissions)
        assert result["dimension_multipliers"]["dim1"] == 1.2


class TestBuildLearningProfileHighScore:
    """Tests for high score dimensions (avg > 8.0)."""

    def test_single_high_score_gets_reduction(self):
        """Single submission with high score gets 0.9 multiplier."""
        submissions = [{"report": {"dimension_scores": {"dim1": {"score": 9.0}}}}]
        result = build_learning_profile(submissions)
        assert result["dimension_multipliers"]["dim1"] == 0.9
        assert "回归均衡" in result["rationale"]["dim1"]

    def test_average_high_score_gets_reduction(self):
        """Multiple submissions averaging above 8.0 get 0.9 multiplier."""
        submissions = [
            {"report": {"dimension_scores": {"dim1": {"score": 8.0}}}},
            {"report": {"dimension_scores": {"dim1": {"score": 9.0}}}},
        ]
        # Average: (8.0 + 9.0) / 2 = 8.5 > 8.0
        result = build_learning_profile(submissions)
        assert result["dimension_multipliers"]["dim1"] == 0.9

    def test_max_score(self):
        """Maximum score (10.0) is handled correctly."""
        submissions = [{"report": {"dimension_scores": {"dim1": {"score": 10.0}}}}]
        result = build_learning_profile(submissions)
        assert result["dimension_multipliers"]["dim1"] == 0.9

    def test_score_just_above_eight(self):
        """Score of 8.01 (just above 8.0) gets reduction."""
        submissions = [{"report": {"dimension_scores": {"dim1": {"score": 8.01}}}}]
        result = build_learning_profile(submissions)
        assert result["dimension_multipliers"]["dim1"] == 0.9


class TestBuildLearningProfileNormalScore:
    """Tests for normal score dimensions (4.0 <= avg <= 8.0)."""

    def test_mid_range_score_stays_neutral(self):
        """Mid-range score (e.g., 6.0) gets 1.0 multiplier."""
        submissions = [{"report": {"dimension_scores": {"dim1": {"score": 6.0}}}}]
        result = build_learning_profile(submissions)
        assert result["dimension_multipliers"]["dim1"] == 1.0
        assert "保持权重" in result["rationale"]["dim1"]

    def test_boundary_four_is_normal(self):
        """Score exactly 4.0 is in normal range."""
        submissions = [{"report": {"dimension_scores": {"dim1": {"score": 4.0}}}}]
        result = build_learning_profile(submissions)
        assert result["dimension_multipliers"]["dim1"] == 1.0

    def test_boundary_eight_is_normal(self):
        """Score exactly 8.0 is in normal range."""
        submissions = [{"report": {"dimension_scores": {"dim1": {"score": 8.0}}}}]
        result = build_learning_profile(submissions)
        assert result["dimension_multipliers"]["dim1"] == 1.0

    def test_average_in_normal_range(self):
        """Multiple submissions averaging in normal range."""
        submissions = [
            {"report": {"dimension_scores": {"dim1": {"score": 3.0}}}},
            {"report": {"dimension_scores": {"dim1": {"score": 9.0}}}},
        ]
        # Average: (3.0 + 9.0) / 2 = 6.0, normal range
        result = build_learning_profile(submissions)
        assert result["dimension_multipliers"]["dim1"] == 1.0


class TestBuildLearningProfileMultipleDimensions:
    """Tests for handling multiple dimensions."""

    def test_multiple_dimensions_different_scores(self):
        """Different dimensions get appropriate multipliers."""
        submissions = [
            {
                "report": {
                    "dimension_scores": {
                        "low_dim": {"score": 2.0},
                        "normal_dim": {"score": 6.0},
                        "high_dim": {"score": 9.0},
                    }
                }
            }
        ]
        result = build_learning_profile(submissions)
        assert result["dimension_multipliers"]["low_dim"] == 1.2
        assert result["dimension_multipliers"]["normal_dim"] == 1.0
        assert result["dimension_multipliers"]["high_dim"] == 0.9

    def test_partial_dimensions_across_submissions(self):
        """Dimensions appearing in some but not all submissions."""
        submissions = [
            {"report": {"dimension_scores": {"dim1": {"score": 3.0}}}},
            {
                "report": {
                    "dimension_scores": {
                        "dim1": {"score": 3.0},
                        "dim2": {"score": 9.0},
                    }
                }
            },
        ]
        result = build_learning_profile(submissions)
        # dim1: (3.0 + 3.0) / 2 = 3.0 < 4.0
        assert result["dimension_multipliers"]["dim1"] == 1.2
        # dim2: 9.0 / 1 = 9.0 > 8.0
        assert result["dimension_multipliers"]["dim2"] == 0.9

    def test_many_dimensions(self):
        """Handles many dimensions correctly."""
        dimension_scores = {f"dim_{i}": {"score": float(i)} for i in range(1, 11)}
        submissions = [{"report": {"dimension_scores": dimension_scores}}]
        result = build_learning_profile(submissions)
        # dim_1 to dim_3: < 4.0 → 1.2
        for i in range(1, 4):
            assert result["dimension_multipliers"][f"dim_{i}"] == 1.2
        # dim_4 to dim_8: 4.0-8.0 → 1.0
        for i in range(4, 9):
            assert result["dimension_multipliers"][f"dim_{i}"] == 1.0
        # dim_9 to dim_10: > 8.0 → 0.9
        for i in range(9, 11):
            assert result["dimension_multipliers"][f"dim_{i}"] == 0.9


class TestBuildLearningProfileEdgeCases:
    """Tests for edge cases and special scenarios."""

    def test_score_as_string_numeric(self):
        """Score provided as string number is converted correctly."""
        submissions = [{"report": {"dimension_scores": {"dim1": {"score": "5.0"}}}}]
        result = build_learning_profile(submissions)
        assert result["dimension_multipliers"]["dim1"] == 1.0

    def test_score_as_integer(self):
        """Score provided as integer is handled correctly."""
        submissions = [{"report": {"dimension_scores": {"dim1": {"score": 5}}}}]
        result = build_learning_profile(submissions)
        assert result["dimension_multipliers"]["dim1"] == 1.0

    def test_missing_score_key_defaults_to_zero(self):
        """Missing score key defaults to 0.0."""
        submissions = [{"report": {"dimension_scores": {"dim1": {"other": "value"}}}}]
        result = build_learning_profile(submissions)
        # 0.0 < 4.0 → 1.2
        assert result["dimension_multipliers"]["dim1"] == 1.2

    def test_many_submissions_averaging(self):
        """Correct averaging across many submissions."""
        # 10 submissions, scores 1-10, average = 5.5
        submissions = [
            {"report": {"dimension_scores": {"dim1": {"score": float(i)}}}} for i in range(1, 11)
        ]
        result = build_learning_profile(submissions)
        assert result["dimension_multipliers"]["dim1"] == 1.0

    def test_output_structure(self):
        """Output has correct structure with both keys."""
        submissions = [{"report": {"dimension_scores": {"dim1": {"score": 5.0}}}}]
        result = build_learning_profile(submissions)
        assert "dimension_multipliers" in result
        assert "rationale" in result
        assert isinstance(result["dimension_multipliers"], dict)
        assert isinstance(result["rationale"], dict)

    def test_rationale_matches_multipliers_keys(self):
        """Rationale keys match multiplier keys exactly."""
        submissions = [
            {
                "report": {
                    "dimension_scores": {
                        "a": {"score": 2.0},
                        "b": {"score": 6.0},
                        "c": {"score": 9.0},
                    }
                }
            }
        ]
        result = build_learning_profile(submissions)
        assert set(result["dimension_multipliers"].keys()) == set(result["rationale"].keys())
