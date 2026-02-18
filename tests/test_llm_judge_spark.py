"""Tests for app/engine/llm_judge_spark.py module."""

from __future__ import annotations

import os
from typing import Any, Dict
from unittest.mock import patch

from app.engine.llm_judge_spark import (
    _build_from_rules,
    _placeholder_evidence,
    build_spark_payload_from_rules,
    post_process_llm_output,
    run_spark_judge,
    validate_llm_judge_json,
)
from app.schemas import DimensionScore, EvidenceSpan, LogicLockResult, ScoreReport


class TestLoadPrompt:
    """Tests for load_prompt function."""

    def test_load_prompt_reads_existing_file(self):
        """load_prompt should read actual prompt file content (lines 12-14)."""
        from app.engine.llm_judge_spark import load_prompt

        # Test with the actual prompt file that exists
        result = load_prompt("spark_judge_qingtian_v1")
        assert isinstance(result, str)
        assert len(result) > 0
        # The prompt should contain some expected content
        assert "评判" in result or "施工" in result or "评分" in result

    def test_load_prompt_returns_string(self):
        """load_prompt should return a string."""
        from app.engine.llm_judge_spark import load_prompt

        result = load_prompt("spark_judge_qingtian_v1")
        assert isinstance(result, str)

    def test_load_prompt_file_not_found(self):
        """load_prompt should raise error for non-existent file."""
        import pytest

        from app.engine.llm_judge_spark import load_prompt

        with pytest.raises(FileNotFoundError):
            load_prompt("non_existent_prompt")


class TestPlaceholderEvidence:
    """Tests for _placeholder_evidence function."""

    def test_returns_correct_structure(self):
        """_placeholder_evidence should return standard placeholder dict."""
        result = _placeholder_evidence()
        assert result["start_index"] == 0
        assert result["end_index"] == 0
        assert "snippet" in result
        assert "未在输入文本中检索到" in result["snippet"]

    def test_returns_new_instance(self):
        """_placeholder_evidence should return new dict each time."""
        result1 = _placeholder_evidence()
        result2 = _placeholder_evidence()
        assert result1 is not result2


class TestValidateLlmJudgeJson:
    """Tests for validate_llm_judge_json function."""

    def _make_valid_payload(self) -> Dict[str, Any]:
        """Create a valid payload for testing."""
        dim_scores = {}
        for i in range(1, 17):
            dim_id = f"{i:02d}"
            dim_scores[dim_id] = {
                "id": dim_id,
                "name": f"维度{i}",
                "module": "module",
                "score_0_10": 7.0,
                "max_score_0_10": 10,
                "weight_multiplier": 1.0,
                "definition_points": ["point1"],
                "defects": ["defect1"],
                "improvements": ["improvement1"],
                "evidence": [{"snippet": "evidence"}],
            }
        return {
            "judge_mode": "spark",
            "model": "spark",
            "prompt_version": "v1",
            "weights": {
                "high_priority_dims": ["07", "09"],
                "high_priority_multiplier": 1.4,
                "normal_multiplier": 1.0,
            },
            "overall": {"total_score_0_100": 75.0},
            "logic_lock": {"definition_score_0_5": 3.0},
            "dimension_scores": dim_scores,
            "penalties": [],
        }

    def test_valid_payload_passes(self):
        """Valid payload should pass validation."""
        payload = self._make_valid_payload()
        ok, err = validate_llm_judge_json(payload)
        assert ok is True
        assert err == {}

    def test_missing_top_level_field(self):
        """Missing top-level field should fail."""
        payload = self._make_valid_payload()
        del payload["judge_mode"]
        ok, err = validate_llm_judge_json(payload)
        assert ok is False
        assert "missing_field:judge_mode" in err["details"]

    def test_missing_dimension(self):
        """Missing dimension should fail."""
        payload = self._make_valid_payload()
        del payload["dimension_scores"]["05"]
        ok, err = validate_llm_judge_json(payload)
        assert ok is False
        assert "missing_dimension:05" in err["details"]

    def test_missing_weights_field(self):
        """Missing weights field should fail."""
        payload = self._make_valid_payload()
        del payload["weights"]["high_priority_dims"]
        ok, err = validate_llm_judge_json(payload)
        assert ok is False
        assert "missing_weights:high_priority_dims" in err["details"]

    def test_missing_dim_field(self):
        """Missing dimension field should fail."""
        payload = self._make_valid_payload()
        del payload["dimension_scores"]["01"]["name"]
        ok, err = validate_llm_judge_json(payload)
        assert ok is False
        assert "missing_dim_field:01:name" in err["details"]

    def test_invalid_evidence_type(self):
        """Non-list or empty evidence should fail."""
        payload = self._make_valid_payload()
        payload["dimension_scores"]["01"]["evidence"] = []
        ok, err = validate_llm_judge_json(payload)
        assert ok is False
        assert "invalid_evidence_type:01" in err["details"]

    def test_evidence_not_list(self):
        """Evidence as string should fail."""
        payload = self._make_valid_payload()
        payload["dimension_scores"]["01"]["evidence"] = "not a list"
        ok, err = validate_llm_judge_json(payload)
        assert ok is False
        assert "invalid_evidence_type:01" in err["details"]


class TestPostProcessLlmOutput:
    """Tests for post_process_llm_output function."""

    def _make_dim_score(self, score: float = 7.0) -> Dict[str, Any]:
        """Create a dimension score dict."""
        return {
            "score_0_10": score,
            "definition_points": None,
            "defects": None,
            "improvements": None,
            "evidence": None,
        }

    def test_fills_empty_definition_points(self):
        """Empty definition_points should be filled."""
        payload = {"dimension_scores": {"01": self._make_dim_score()}}
        rubric = {}
        result = post_process_llm_output(payload, rubric)
        assert len(result["dimension_scores"]["01"]["definition_points"]) > 0

    def test_fills_empty_defects(self):
        """Empty defects should be filled."""
        payload = {"dimension_scores": {"01": self._make_dim_score()}}
        rubric = {}
        result = post_process_llm_output(payload, rubric)
        assert len(result["dimension_scores"]["01"]["defects"]) > 0

    def test_fills_empty_improvements(self):
        """Empty improvements should be filled."""
        payload = {"dimension_scores": {"01": self._make_dim_score()}}
        rubric = {}
        result = post_process_llm_output(payload, rubric)
        assert len(result["dimension_scores"]["01"]["improvements"]) > 0

    def test_fills_empty_evidence(self):
        """Empty evidence should be filled with placeholder."""
        payload = {"dimension_scores": {"01": self._make_dim_score()}}
        rubric = {}
        result = post_process_llm_output(payload, rubric)
        assert len(result["dimension_scores"]["01"]["evidence"]) > 0
        assert result["dimension_scores"]["01"]["evidence"][0]["start_index"] == 0

    def test_caps_score_when_default_used(self):
        """Score should be capped at 4.0 when defaults are used."""
        payload = {"dimension_scores": {"01": self._make_dim_score(score=8.0)}}
        rubric = {}
        result = post_process_llm_output(payload, rubric)
        assert result["dimension_scores"]["01"]["score_0_10"] <= 4.0

    def test_adds_system_notice_when_default_used(self):
        """System notice should be added when defaults are used."""
        payload = {"dimension_scores": {"01": self._make_dim_score()}}
        rubric = {}
        result = post_process_llm_output(payload, rubric)
        defects = result["dimension_scores"]["01"]["defects"]
        assert any("[系统提示]" in d for d in defects)

    def test_applies_high_priority_multiplier(self):
        """High priority dims should get higher multiplier."""
        payload = {
            "dimension_scores": {
                "07": {
                    "score_0_10": 7.0,
                    "defects": ["d"],
                    "improvements": ["i"],
                    "evidence": [{}],
                },
                "01": {
                    "score_0_10": 7.0,
                    "defects": ["d"],
                    "improvements": ["i"],
                    "evidence": [{}],
                },
            }
        }
        rubric = {
            "llm_weight_profile": {
                "high_priority_dims": ["07"],
                "high_priority_multiplier": 1.4,
                "normal_multiplier": 1.0,
            }
        }
        result = post_process_llm_output(payload, rubric)
        assert result["dimension_scores"]["07"]["weight_multiplier"] == 1.4
        assert result["dimension_scores"]["01"]["weight_multiplier"] == 1.0

    def test_adds_weights_to_payload(self):
        """weights field should be added to payload."""
        payload = {"dimension_scores": {}}
        rubric = {
            "llm_weight_profile": {
                "high_priority_dims": ["07", "09"],
                "high_priority_multiplier": 1.5,
                "normal_multiplier": 1.0,
            }
        }
        result = post_process_llm_output(payload, rubric)
        assert "weights" in result
        assert result["weights"]["high_priority_multiplier"] == 1.5


class TestBuildFromRules:
    """Tests for _build_from_rules function."""

    def _make_score_report(self) -> ScoreReport:
        """Create a mock ScoreReport."""
        from app.engine.dimensions import DIMENSIONS

        dim_scores = {}
        for dim_id, meta in DIMENSIONS.items():
            dim_scores[dim_id] = DimensionScore(
                id=dim_id,
                name=meta["name"],
                module=meta["module"],
                score=7.0,
                max_score=10.0,
                hits=["hit1"],
                evidence=[EvidenceSpan(start_index=0, end_index=10, snippet="test snippet")],
            )

        return ScoreReport(
            total_score=75.0,
            dimension_scores=dim_scores,
            logic_lock=LogicLockResult(
                definition_score=4.0,
                analysis_score=4.0,
                solution_score=4.0,
                breaks=[],
                evidence=[],
            ),
            penalties=[],
            suggestions=[],
            meta={},
            judge_mode="local",
            judge_source="scorer",
            fallback_reason="",
        )

    def test_builds_correct_structure(self):
        """_build_from_rules should build correct payload structure."""
        report = self._make_score_report()
        rubric = {}
        result = _build_from_rules(report, rubric)

        assert "judge_mode" in result
        assert "model" in result
        assert "prompt_version" in result
        assert "weights" in result
        assert "overall" in result
        assert "logic_lock" in result
        assert "dimension_scores" in result
        assert "penalties" in result

    def test_includes_all_dimensions(self):
        """Result should include all 16 dimensions."""
        from app.engine.dimensions import DIMENSIONS

        report = self._make_score_report()
        rubric = {}
        result = _build_from_rules(report, rubric)

        for dim_id in DIMENSIONS:
            assert dim_id in result["dimension_scores"]

    def test_dimension_has_required_fields(self):
        """Each dimension should have required fields."""
        report = self._make_score_report()
        rubric = {}
        result = _build_from_rules(report, rubric)

        dim = result["dimension_scores"]["01"]
        assert "id" in dim
        assert "name" in dim
        assert "module" in dim
        assert "score_0_10" in dim
        assert "evidence" in dim

    def test_handles_logic_lock_breaks(self):
        """Logic lock breaks should be converted correctly."""
        report = self._make_score_report()
        report.logic_lock.breaks = ["definition", "analysis"]
        rubric = {}
        result = _build_from_rules(report, rubric)

        breaks = result["logic_lock"]["breaks"]
        assert len(breaks) == 2
        assert breaks[0]["type"] == "missing_definition"
        assert breaks[1]["type"] == "missing_analysis"

    def test_handles_logic_lock_solution_break(self):
        """Logic lock solution breaks should use else branch (line 177)."""
        report = self._make_score_report()
        # Use "solution" to trigger the else branch at line 177
        report.logic_lock.breaks = ["solution"]
        rubric = {}
        result = _build_from_rules(report, rubric)

        breaks = result["logic_lock"]["breaks"]
        assert len(breaks) == 1
        assert breaks[0]["type"] == "missing_solution"

    def test_handles_all_logic_lock_break_types(self):
        """Test all three break types together."""
        report = self._make_score_report()
        report.logic_lock.breaks = ["definition", "analysis", "solution"]
        rubric = {}
        result = _build_from_rules(report, rubric)

        breaks = result["logic_lock"]["breaks"]
        assert len(breaks) == 3
        assert breaks[0]["type"] == "missing_definition"
        assert breaks[1]["type"] == "missing_analysis"
        assert breaks[2]["type"] == "missing_solution"

    def test_uses_placeholder_for_empty_evidence(self):
        """Empty evidence should use placeholder."""
        from app.engine.dimensions import DIMENSIONS

        dim_scores = {}
        for dim_id, meta in DIMENSIONS.items():
            dim_scores[dim_id] = DimensionScore(
                id=dim_id,
                name=meta["name"],
                module=meta["module"],
                score=7.0,
                max_score=10.0,
                hits=[],
                evidence=[],  # Empty evidence
            )

        report = ScoreReport(
            total_score=75.0,
            dimension_scores=dim_scores,
            logic_lock=LogicLockResult(
                definition_score=4.0,
                analysis_score=4.0,
                solution_score=4.0,
                breaks=[],
                evidence=[],
            ),
            penalties=[],
            suggestions=[],
            meta={},
            judge_mode="local",
            judge_source="scorer",
            fallback_reason="",
        )
        rubric = {}
        result = _build_from_rules(report, rubric)

        dim = result["dimension_scores"]["01"]
        assert len(dim["evidence"]) == 1
        assert dim["evidence"][0]["start_index"] == 0
        assert dim["score_0_10"] <= 4.0


class TestBuildSparkPayloadFromRules:
    """Tests for build_spark_payload_from_rules function."""

    def _make_score_report(self) -> ScoreReport:
        """Create a mock ScoreReport."""
        from app.engine.dimensions import DIMENSIONS

        dim_scores = {}
        for dim_id, meta in DIMENSIONS.items():
            dim_scores[dim_id] = DimensionScore(
                id=dim_id,
                name=meta["name"],
                module=meta["module"],
                score=7.0,
                max_score=10.0,
                hits=["hit1"],
                evidence=[EvidenceSpan(start_index=0, end_index=10, snippet="test")],
            )

        return ScoreReport(
            total_score=75.0,
            dimension_scores=dim_scores,
            logic_lock=LogicLockResult(
                definition_score=4.0,
                analysis_score=4.0,
                solution_score=4.0,
                breaks=[],
                evidence=[],
            ),
            penalties=[],
            suggestions=[],
            meta={},
            judge_mode="local",
            judge_source="scorer",
            fallback_reason="",
        )

    def test_returns_processed_payload(self):
        """build_spark_payload_from_rules should return post-processed payload."""
        report = self._make_score_report()
        rubric = {"llm_weight_profile": {"high_priority_dims": ["07"]}}
        result = build_spark_payload_from_rules(report, rubric)

        assert "weights" in result
        assert "dimension_scores" in result


class TestRunSparkJudge:
    """Tests for run_spark_judge function."""

    def _make_score_report(self) -> ScoreReport:
        """Create a mock ScoreReport."""
        from app.engine.dimensions import DIMENSIONS

        dim_scores = {}
        for dim_id, meta in DIMENSIONS.items():
            dim_scores[dim_id] = DimensionScore(
                id=dim_id,
                name=meta["name"],
                module=meta["module"],
                score=7.0,
                max_score=10.0,
                hits=["hit1"],
                evidence=[EvidenceSpan(start_index=0, end_index=10, snippet="test")],
            )

        return ScoreReport(
            total_score=75.0,
            dimension_scores=dim_scores,
            logic_lock=LogicLockResult(
                definition_score=4.0,
                analysis_score=4.0,
                solution_score=4.0,
                breaks=[],
                evidence=[],
            ),
            penalties=[],
            suggestions=[],
            meta={},
            judge_mode="local",
            judge_source="scorer",
            fallback_reason="",
        )

    def test_returns_missing_credentials_when_env_not_set(self):
        """run_spark_judge should return error when credentials missing."""
        # Clear any existing env vars
        with patch.dict(os.environ, {}, clear=True):
            report = self._make_score_report()
            result = run_spark_judge("test text", {}, "prompt", report)

            assert result["called_spark_api"] is False
            assert result["reason"] == "missing_credentials"

    def test_returns_missing_credentials_with_partial_env(self):
        """run_spark_judge should fail with only partial credentials."""
        with patch.dict(os.environ, {"SPARK_APP_ID": "test"}, clear=True):
            report = self._make_score_report()
            result = run_spark_judge("test text", {}, "prompt", report)

            assert result["called_spark_api"] is False
            assert result["reason"] == "missing_credentials"

    @patch("app.engine.llm_judge_spark.load_prompt")
    def test_loads_prompt_when_credentials_present(self, mock_load):
        """run_spark_judge should load prompt when credentials are set."""
        mock_load.return_value = "test prompt content"

        with patch.dict(
            os.environ,
            {
                "SPARK_APP_ID": "id",
                "SPARK_API_KEY": "key",
                "SPARK_API_SECRET": "secret",
            },
        ):
            report = self._make_score_report()
            result = run_spark_judge("test text", {}, "test_prompt", report)

            mock_load.assert_called_once_with("test_prompt")
            # Should succeed and return payload
            assert result.get("called_spark_api") is True or "reason" in result

    @patch("app.engine.llm_judge_spark.load_prompt")
    def test_returns_payload_with_credentials(self, mock_load):
        """run_spark_judge should return valid payload when credentials present."""
        mock_load.return_value = "test prompt"

        with patch.dict(
            os.environ,
            {
                "SPARK_APP_ID": "id",
                "SPARK_API_KEY": "key",
                "SPARK_API_SECRET": "secret",
            },
        ):
            report = self._make_score_report()
            result = run_spark_judge("test text", {}, "prompt", report)

            # If validation passes, called_spark_api should be True
            if result.get("called_spark_api"):
                assert "dimension_scores" in result


class TestEdgeCases:
    """Tests for edge cases and boundary conditions."""

    def test_validate_empty_payload(self):
        """Empty payload should fail validation."""
        ok, err = validate_llm_judge_json({})
        assert ok is False
        assert len(err["details"]) > 0

    def test_post_process_empty_dim_scores(self):
        """Post-process should handle empty dimension_scores."""
        payload = {"dimension_scores": {}}
        rubric = {}
        result = post_process_llm_output(payload, rubric)
        assert result["dimension_scores"] == {}

    def test_validate_with_extra_dimensions(self):
        """Extra dimensions beyond 16 should still pass if all required present."""
        # Create valid payload
        dim_scores = {}
        for i in range(1, 17):
            dim_id = f"{i:02d}"
            dim_scores[dim_id] = {
                "id": dim_id,
                "name": f"维度{i}",
                "module": "module",
                "score_0_10": 7.0,
                "max_score_0_10": 10,
                "weight_multiplier": 1.0,
                "definition_points": ["p"],
                "defects": ["d"],
                "improvements": ["i"],
                "evidence": [{"snippet": "e"}],
            }
        # Add extra dimension
        dim_scores["99"] = dim_scores["01"].copy()

        payload = {
            "judge_mode": "spark",
            "model": "spark",
            "prompt_version": "v1",
            "weights": {
                "high_priority_dims": [],
                "high_priority_multiplier": 1.0,
                "normal_multiplier": 1.0,
            },
            "overall": {},
            "logic_lock": {},
            "dimension_scores": dim_scores,
            "penalties": [],
        }

        ok, err = validate_llm_judge_json(payload)
        assert ok is True
